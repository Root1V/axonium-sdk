package axonium

import (
	"bufio"
	"context"
	"io"
	"net/http"
	"sync"
)

// ChatCompletionStream is an open streaming completion.
//
// Iterate with Next and Current, then check Err. Always Close it: closing is what propagates
// cancellation to the gateway and on to Prometheus, which stops the generation and frees the
// backend slot. A stream that is merely abandoned keeps the GPU busy and keeps billing.
//
//	stream, err := client.Chat.Stream(ctx, req)
//	if err != nil { return err }
//	defer stream.Close()
//	for stream.Next() {
//		fmt.Print(stream.Current().Content())
//	}
//	return stream.Err()
type ChatCompletionStream struct {
	scanner *bufio.Scanner
	body    io.ReadCloser
	cancel  context.CancelFunc
	meta    ResponseMeta
	acc     accumulator

	current *ChatCompletionChunk
	err     error

	closeOnce sync.Once
}

func newChatCompletionStream(resp *http.Response, meta ResponseMeta, cancel context.CancelFunc) *ChatCompletionStream {
	scanner := bufio.NewScanner(resp.Body)
	// A single SSE line can carry a whole chunk; the default 64KiB ceiling is not generous enough
	// for a reasoning model's larger deltas.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	scanner.Split(scanSSE)

	return &ChatCompletionStream{
		scanner: scanner,
		body:    resp.Body,
		cancel:  cancel,
		meta:    meta,
	}
}

// Next advances to the next chunk, returning false at end of stream or on error.
func (s *ChatCompletionStream) Next() bool {
	if s.err != nil || s.acc.done {
		return false
	}

	for s.scanner.Scan() {
		event, ok := decodeLine(s.scanner.Text())
		if !ok {
			continue
		}
		if event.done {
			s.acc.done = true
			return false
		}

		chunk, err := s.acc.feed(event, s.meta)
		if err != nil {
			s.err = err
			return false
		}
		s.current = chunk
		return true
	}

	if err := s.scanner.Err(); err != nil {
		s.err = translateTransportError(context.Background(), err)
		return false
	}

	// The scanner ended without the sentinel. The gateway always appends it, so its absence means
	// the connection died mid-generation -- which is a failure, not a completion, and saying so is
	// the difference between a caller seeing a truncated answer and believing a complete one.
	if !s.acc.done {
		s.err = &StreamError{
			Message:        "the connection closed before the stream was terminated",
			PartialContent: s.acc.content.String(),
			RequestID:      s.meta.RequestID,
			TraceID:        s.meta.TraceID,
		}
	}
	return false
}

// Current returns the chunk most recently produced by Next.
func (s *ChatCompletionStream) Current() *ChatCompletionChunk { return s.current }

// Err returns the error that ended the stream, or nil if it completed normally.
func (s *ChatCompletionStream) Err() error { return s.err }

// Content returns everything received so far, including on a stream that failed partway.
func (s *ChatCompletionStream) Content() string { return s.acc.content.String() }

// Reasoning returns the chain of thought received so far, assembled separately from the answer.
//
// A reasoning model streams its thinking before any answer token and keeps it in its own field, so
// Content stays empty until it stops thinking. With a small max_tokens it can stay empty for the
// whole stream, with FinishReason "length" -- and there is still usage to pay for. Neither field is
// inferred from the other.
func (s *ChatCompletionStream) Reasoning() string { return s.acc.reasoning.String() }

// Usage returns token accounting for the stream, or nil if it cannot be determined. Check
// Usage.Estimated: llama.cpp-family backends report no usage at all when streaming, so the counts
// may be derived from timings rather than measured.
func (s *ChatCompletionStream) Usage() *Usage { return s.acc.finalUsage() }

// Meta returns the correlation IDs and rate-limit budget of the response that opened this stream.
func (s *ChatCompletionStream) Meta() ResponseMeta { return s.meta }

// Close releases the stream and cancels the underlying request.
//
// Cancelling the request context aborts the body read and closes the HTTP connection, which
// Prometheus observes as a client disconnect and responds to by stopping generation. That is what
// makes an abandoned stream stop costing money, so Close is not a formality.
func (s *ChatCompletionStream) Close() error {
	s.closeOnce.Do(func() {
		// Cancel before closing: the cancel is what tears the connection down rather than letting
		// it be returned to the pool with a generation still running behind it.
		if s.cancel != nil {
			s.cancel()
		}
		if s.body != nil {
			_ = s.body.Close()
		}
	})
	return nil
}
