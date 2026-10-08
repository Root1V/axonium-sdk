package axonium

import (
	"context"
	"io"
	"log/slog"
	"net/url"
	"strings"
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

// providerName is gen_ai.provider.name, which replaced the deprecated gen_ai.system.
//
// The convention's enumeration names model providers -- openai, anthropic, groq and so on -- and
// this platform is not one of them, so this is a CUSTOM value, which the convention permits when no
// well-known one applies. It deliberately does not name the inference engine: Argus maps this
// attribute to llama.cpp / vllm / ollama, which is right for the gateway's own spans because the
// gateway knows which backend served the request, and impossible from a client because no response
// header carries it. server.address goes on the span instead, which is what the convention names as
// the way to identify the actual system behind an OpenAI-compatible endpoint.
const providerName = "prometheus-gateway"

// operationName maps a route to gen_ai.operation.name, or "" where no GenAI operation happened.
//
// The shared table is spec/otel-genai.json and TestOperationNamesMatchTheSpec asserts this against
// it. Three SDKs emit spans; a table copied by hand into three languages is how AXO-139 produced
// five divergent rate-limit scope lists that nothing could catch.
func operationName(path string) string {
	switch {
	case strings.HasSuffix(path, "/chat/completions"):
		return "chat"
	case strings.HasSuffix(path, "/embeddings"):
		return "embeddings"
	// Not "retrieval": that well-known value is RAG retrieval and carries gen_ai.data_source.id to
	// say which corpus was read. A reranker reads no corpus; it scores documents the caller holds.
	case strings.HasSuffix(path, "/rerank"):
		return "rerank"
	// The enumeration has no image value, and generate_content means multimodal generation in
	// Gemini-shaped APIs.
	case strings.HasSuffix(path, "/generations"):
		return "image_generation"
	case strings.HasSuffix(path, "/predict"):
		return "predict"
	}
	// /v1/models and /v1/models/mine reach no model, so a gen_ai.* span for them would carry a null
	// model and an operation name the convention does not have. Until this returned "", they got
	// gen_ai.system and a null model, which put a scope lookup in GenAI aggregations as an
	// inference call that somehow used no tokens.
	return ""
}

// startSpan begins a span for one operation, or returns a no-op when tracing is off.
//
// The span is named {operation} {model} -- "chat qwen3-0.6b" -- which is the convention's rule, and
// the one Argus asked for explicitly when Prometheus offered to depart from it to keep cardinality
// down: follow the standard, the cardinality is theirs to solve. Grouping by model then needs no
// special query.
func (c *Client) startSpan(ctx context.Context, method, path, model string) (context.Context, Span) {
	if c.config.Tracer == nil {
		return ctx, noopSpan{}
	}

	attrs := map[string]any{
		"http.request.method": method,
		"url.path":            path,
		"server.address":      hostOf(c.config.GatewayBaseURL),
	}

	operation := operationName(path)
	if operation == "" {
		return c.config.Tracer.StartSpan(ctx, "axonium "+method+" "+path, attrs)
	}

	attrs["gen_ai.provider.name"] = providerName
	attrs["gen_ai.operation.name"] = operation
	attrs["gen_ai.request.model"] = model

	name := operation
	if model != "" {
		name = operation + " " + model
	}
	return c.config.Tracer.StartSpan(ctx, name, attrs)
}

// hostOf is server.address: the host of the gateway, without scheme or port path noise. A base URL
// that will not parse yields "" rather than an error, because a malformed span attribute must never
// be the reason a request does not happen.
func hostOf(base string) string {
	parsed, err := url.Parse(base)
	if err != nil {
		return ""
	}
	return parsed.Hostname()
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

// noticeableWait is the wait at or above which a retry is reported at INFO rather than DEBUG: long
// enough that a caller will notice it as a stall and want it explained.
const noticeableWait = time.Second

// logRetryWait reports that the SDK is about to sleep before retrying, and for how long.
//
// A caller who sees a call take 45 seconds and finds nothing in their logs files a latency bug. The
// 429 was the rate limit working and the wait is the whole explanation, so a wait a person would
// notice is reported at INFO. Sub-second backoff stays at DEBUG, where it belongs: the noise worry
// is frequent small retries, not the rare long one.
//
// Recorded as a wait rather than folded into duration_ms, which is measured per attempt and
// deliberately excludes it: time spent sleeping is not latency.
func (c *Client) logRetryWait(model string, status, attempt int, suffix string, delay time.Duration) {
	attrs := []any{
		slog.Float64("delay_s", delay.Seconds()),
		slog.Int("attempt", attempt),
	}
	if model != "" {
		attrs = append(attrs, slog.String("model", model))
	}
	if status != 0 {
		attrs = append(attrs, slog.Int("status", status))
	}
	if suffix != "" {
		attrs = append(attrs, slog.String("type", suffix))
	}

	if delay >= noticeableWait {
		c.logger().Info("axonium waiting before a retry", attrs...)
		return
	}
	c.logger().Debug("axonium waiting before a retry", attrs...)
}
