package axonium

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// recordingTracer is the few lines a host writes to feed Axonium's spans into its own tracing.
type recordingTracer struct {
	mu    sync.Mutex
	spans []*recordingSpan
}

type recordingSpan struct {
	mu    sync.Mutex
	name  string
	attrs map[string]any
	errs  []error
	ended bool
}

func (t *recordingTracer) StartSpan(ctx context.Context, name string, attrs map[string]any) (context.Context, Span) {
	s := &recordingSpan{name: name, attrs: map[string]any{}}
	for k, v := range attrs {
		s.attrs[k] = v
	}
	t.mu.Lock()
	t.spans = append(t.spans, s)
	t.mu.Unlock()
	return ctx, s
}

func (s *recordingSpan) SetAttributes(attrs map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, v := range attrs {
		s.attrs[k] = v
	}
}
func (s *recordingSpan) RecordError(err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.errs = append(s.errs, err)
}
func (s *recordingSpan) End() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.ended = true
}

func observedServer(t *testing.T, fail bool) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		w.Header().Set("X-Request-ID", "req-42")
		w.Header().Set("X-Trace-ID", "trace-42")
		w.Header().Set("X-Prometheus-Instance-Id", "qwen3-0-6b-iq4-nl-local-1")
		if fail {
			problemJSON(w, 400, "unknown-model", "no such model")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "c1", "model": "qwen3-0.6b",
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "ok"}}},
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

// The promise that makes the whole record set safe to emit at any level: nothing in a log line is
// content. A library that can be configured to log prompts is how prompts end up in an aggregator
// nobody audited, so there is no such configuration.
func TestLogsCarryMetadataAndNeverContent(t *testing.T) {
	var buf bytes.Buffer
	srv := observedServer(t, false)

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		ClientID: "id-should-not-appear", ClientSecret: "secret-should-not-appear",
		Logger: slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})),
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	req := simpleRequest()
	req.Messages = []Message{TextMessage("user", "a very secret prompt about payroll")}
	if _, err := client.Chat.Create(context.Background(), req); err != nil {
		t.Fatalf("create: %v", err)
	}

	logged := buf.String()
	for _, want := range []string{"request_id=req-42", "trace_id=trace-42", "status=200",
		"attempt=1", "model=llama3-8b-q4", "duration_ms=", "instance_id="} {
		if !strings.Contains(logged, want) {
			t.Errorf("the record should carry %q\ngot: %s", want, logged)
		}
	}
	for _, forbidden := range []string{"secret prompt", "payroll", "secret-should-not-appear",
		"id-should-not-appear", "Bearer", "ok"} {
		if strings.Contains(logged, forbidden) {
			t.Errorf("the record must never carry %q\ngot: %s", forbidden, logged)
		}
	}
}

func TestNoLoggerMeansSilence(t *testing.T) {
	// A library does not decide a host's logging. With none supplied it writes nowhere, and the
	// call must work exactly the same.
	srv := observedServer(t, false)
	client := testClient(t, srv.URL)
	if _, err := client.Chat.Create(context.Background(), simpleRequest()); err != nil {
		t.Fatalf("a client without a logger must work normally: %v", err)
	}
}

func TestSpansCarryTheCorrelationIDs(t *testing.T) {
	tracer := &recordingTracer{}
	srv := observedServer(t, false)

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		ClientID: "i", ClientSecret: "s", Tracer: tracer,
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	if _, err := client.Chat.Create(context.Background(), simpleRequest()); err != nil {
		t.Fatalf("create: %v", err)
	}

	if len(tracer.spans) != 1 {
		t.Fatalf("expected one span, got %d", len(tracer.spans))
	}
	span := tracer.spans[0]
	if span.name != "chat.completions" {
		t.Errorf("a span should name the operation, not the URL; got %q", span.name)
	}
	if !span.ended {
		t.Error("the span was never ended")
	}
	// This is the whole point of the span: matching a caller's trace to the platform's.
	for key, want := range map[string]any{
		"gen_ai.system":          "prometheus-gateway",
		"gen_ai.request.model":   "llama3-8b-q4",
		"prometheus.request_id":  "req-42",
		"prometheus.trace_id":    "trace-42",
		"prometheus.instance_id": "qwen3-0-6b-iq4-nl-local-1",
	} {
		if span.attrs[key] != want {
			t.Errorf("%s: got %v, want %v", key, span.attrs[key], want)
		}
	}
	if len(span.errs) != 0 {
		t.Errorf("a successful call recorded errors: %v", span.errs)
	}
}

func TestAFailedCallIsRecordedOnTheSpanAndLogged(t *testing.T) {
	var buf bytes.Buffer
	tracer := &recordingTracer{}
	srv := observedServer(t, true)

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s",
		Tracer: tracer, Retry: fastRetry(),
		Logger: slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})),
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	_, err = client.Chat.Create(context.Background(), simpleRequest())
	if !errors.Is(err, ErrUnknownModel) {
		t.Fatalf("expected the gateway's error, got %v", err)
	}

	span := tracer.spans[0]
	if len(span.errs) == 0 {
		t.Error("a failed call should be recorded on the span")
	}
	if !span.ended {
		t.Error("the span must end even when the call fails")
	}
	logged := buf.String()
	if !strings.Contains(logged, "status=400") || !strings.Contains(logged, "request_id=req-42") {
		t.Errorf("a failure should log its status and correlation id\ngot: %s", logged)
	}
}

func TestNoTracerMeansNoSpans(t *testing.T) {
	// The no-op keeps the call sites free of branching, so tracing off costs nothing.
	client := testClient(t, observedServer(t, false).URL)
	ctx, span := client.startSpan(context.Background(), "chat.completions", "m")
	if ctx == nil || span == nil {
		t.Fatal("a no-op span must still be usable")
	}
	span.SetAttributes(map[string]any{"k": "v"})
	span.RecordError(errors.New("x"))
	span.End()
}
