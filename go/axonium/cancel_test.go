package axonium

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// Cancelling a stream must reach the far end.
//
// This is the acceptance condition Aeon set before replacing their native prometheus_inference
// adapter with this SDK: if cancelling only abandons the local iterator while the GPU keeps
// generating, the swap is a regression. So the assertion here is deliberately not about the
// reader -- a local loop exiting proves nothing, since the generation can finish upstream and be
// discarded below. The server counts how many chunks it produced before the client went away.

// streamingServer serves an endless SSE stream and records how many chunks it managed to write
// before the connection died.
func streamingServer(t *testing.T, produced *int64, gone chan<- struct{}) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok-stream", 300)
			return
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("X-Request-ID", "req-stream")
		w.WriteHeader(http.StatusOK)
		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Error("the test server cannot flush, so it cannot stream")
			return
		}

		for i := 0; i < 5000; i++ {
			select {
			case <-r.Context().Done():
				// The client's disconnect observed at the server: this is the signal Prometheus
				// acts on when it stops generating.
				close(gone)
				return
			default:
			}

			fmt.Fprintf(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"%d \"}}]}\n\n", i)
			flusher.Flush()
			atomic.AddInt64(produced, 1)
			time.Sleep(time.Millisecond)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestCloseStopsUpstreamGeneration(t *testing.T) {
	var produced int64
	gone := make(chan struct{})
	srv := streamingServer(t, &produced, gone)
	client := testClient(t, srv.URL)

	stream, err := client.Chat.Stream(context.Background(), simpleRequest())
	if err != nil {
		t.Fatalf("opening the stream: %v", err)
	}

	read := 0
	for stream.Next() {
		read++
		if read == 3 {
			break
		}
	}
	if read != 3 {
		t.Fatalf("wanted 3 chunks before closing, read %d (err: %v)", read, stream.Err())
	}

	if err := stream.Close(); err != nil {
		t.Fatalf("closing: %v", err)
	}

	select {
	case <-gone:
	case <-time.After(5 * time.Second):
		t.Fatal("the server never observed the disconnect: closing the stream did not reach the far end, so a cancelled generation would keep running and keep billing")
	}

	atStop := atomic.LoadInt64(&produced)
	time.Sleep(150 * time.Millisecond)
	if after := atomic.LoadInt64(&produced); after != atStop {
		t.Fatalf("the server kept generating after the disconnect: %d chunks at stop, %d after", atStop, after)
	}
	t.Logf("upstream stopped after %d chunks; the client read %d", atStop, read)
}

func TestContextCancellationStopsUpstreamGeneration(t *testing.T) {
	// The same property through the other door. Go callers reach for ctx cancellation, and a
	// consumer who wrote `defer stream.Close()` should not have to rewrite it to get propagation:
	// both must work.
	var produced int64
	gone := make(chan struct{})
	srv := streamingServer(t, &produced, gone)
	client := testClient(t, srv.URL)

	ctx, cancel := context.WithCancel(context.Background())
	stream, err := client.Chat.Stream(ctx, simpleRequest())
	if err != nil {
		cancel()
		t.Fatalf("opening the stream: %v", err)
	}
	defer stream.Close()

	if !stream.Next() {
		cancel()
		t.Fatalf("expected at least one chunk, got err: %v", stream.Err())
	}
	cancel()

	select {
	case <-gone:
	case <-time.After(5 * time.Second):
		t.Fatal("cancelling the context did not close the connection to the gateway")
	}
}

func TestStreamEndingWithoutSentinelIsAFailure(t *testing.T) {
	// The gateway always appends [DONE]. Its absence means the connection died mid-generation, so
	// reporting success would hand the caller a truncated answer they believe is complete.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"half\"}}]}\n\n")
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	stream, err := client.Chat.Stream(context.Background(), simpleRequest())
	if err != nil {
		t.Fatalf("opening the stream: %v", err)
	}
	defer stream.Close()

	for stream.Next() {
	}

	if !errors.Is(stream.Err(), ErrStreamInterrupted) {
		t.Fatalf("a stream that ended without the sentinel should be an interruption, got: %v", stream.Err())
	}
	var streamErr *StreamError
	if errors.As(stream.Err(), &streamErr) && streamErr.PartialContent != "half" {
		t.Fatalf("the partial content should survive the failure, got %q", streamErr.PartialContent)
	}
}
