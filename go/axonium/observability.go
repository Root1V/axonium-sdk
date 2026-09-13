package axonium

import (
	"context"
	"io"
	"log/slog"
	"time"
)

// Observability here is deliberately the complementary half: the platform owns tracing, and this
// emits only what a caller's own traces and logs need to line up with it.
//
// Prompts, completions and credentials are NEVER logged or recorded on a span, and there is no
// option to turn that on. Correlating a request with the platform's traces needs the request and
// trace IDs, not the content -- and a library that can be configured to log prompts is how that
// content ends up in a log aggregator nobody audited. Anyone who needs the payload has it at the
// call site already.

// discardLogger is the default: the SDK stays silent until the host application supplies one.
// Handlers, levels and formatting are the application's decision, never the library's.
var discardLogger = slog.New(slog.NewTextHandler(io.Discard, nil))

// Span is one traced operation. Implement it alongside Tracer to feed Axonium's spans into
// whatever tracing the host already runs.
type Span interface {
	// SetAttributes adds metadata to the span. Only ever metadata about the exchange.
	SetAttributes(attrs map[string]any)
	// RecordError marks the operation as failed.
	RecordError(err error)
	// End closes the span.
	End()
}

// Tracer starts spans for Axonium operations.
//
// This is an interface rather than a dependency on OpenTelemetry on purpose. This module has no
// third-party dependencies, which is a property worth keeping for something other people vendor:
// importing an OTel SDK here would put it in the dependency tree of every consumer, including the
// ones who trace with something else or not at all. Wiring it is a few lines on your side:
//
//	type otelTracer struct{ tracer trace.Tracer }
//
//	func (t otelTracer) StartSpan(ctx context.Context, name string, attrs map[string]any) (context.Context, axonium.Span) {
//		ctx, span := t.tracer.Start(ctx, name)
//		return ctx, otelSpan{span}
//	}
//
// Attribute names follow the GenAI semantic conventions, so the spans are readable by tooling that
// already understands LLM traffic.
//
// Trace context is NOT propagated outbound: the gateway does not read traceparent, so sending one
// would be decoration. Correlation runs inbound instead, through the request and trace IDs the
// gateway returns, which are recorded on the span when the response arrives.
type Tracer interface {
	StartSpan(ctx context.Context, name string, attrs map[string]any) (context.Context, Span)
}

// noopSpan is what a call gets when no Tracer is configured, so call sites need no branching.
type noopSpan struct{}

func (noopSpan) SetAttributes(map[string]any) {}
func (noopSpan) RecordError(error)            {}
func (noopSpan) End()                         {}

// startSpan begins a span for one operation, or returns a no-op when tracing is off.
func (c *Client) startSpan(ctx context.Context, name, model string) (context.Context, Span) {
	if c.config.Tracer == nil {
		return ctx, noopSpan{}
	}
	return c.config.Tracer.StartSpan(ctx, name, map[string]any{
		"gen_ai.system":        "prometheus-gateway",
		"gen_ai.request.model": model,
	})
}

// recordResponse attaches the gateway's correlation IDs to a span, which is the whole point of the
// span existing: it is what lets a caller's trace be matched against the platform's.
func recordResponse(span Span, meta ResponseMeta) {
	attrs := map[string]any{}
	if meta.RequestID != "" {
		attrs["prometheus.request_id"] = meta.RequestID
	}
	if meta.TraceID != "" {
		attrs["prometheus.trace_id"] = meta.TraceID
	}
	if meta.InstanceID != "" {
		attrs["prometheus.instance_id"] = meta.InstanceID
	}
	if len(attrs) > 0 {
		span.SetAttributes(attrs)
	}
}

// logRequest emits one structured record per completed attempt. Every field is metadata about the
// exchange rather than its content, which is what makes the whole set safe to emit at any level.
func (c *Client) logRequest(method, path, model string, status, attempt int, started time.Time, meta ResponseMeta, err error) {
	attrs := []any{
		slog.String("method", method),
		slog.String("path", path),
		slog.Float64("duration_ms", float64(time.Since(started).Microseconds())/1000),
		slog.Int("attempt", attempt),
	}
	if model != "" {
		attrs = append(attrs, slog.String("model", model))
	}
	if status != 0 {
		attrs = append(attrs, slog.Int("status", status))
	}
	if meta.RequestID != "" {
		attrs = append(attrs, slog.String("request_id", meta.RequestID))
	}
	if meta.TraceID != "" {
		attrs = append(attrs, slog.String("trace_id", meta.TraceID))
	}
	if meta.InstanceID != "" {
		attrs = append(attrs, slog.String("instance_id", meta.InstanceID))
	}

	if err != nil {
		c.logger().Debug("axonium request failed", append(attrs, slog.String("error", err.Error()))...)
		return
	}
	c.logger().Debug("axonium request", attrs...)
}

func (c *Client) logger() *slog.Logger {
	if c.config.Logger == nil {
		return discardLogger
	}
	return c.config.Logger
}
