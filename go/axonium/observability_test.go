package axonium

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
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
	// Attributes offered after End, which a real span discards. Recorded rather than ignored so a
	// test can report what was lost.
	droppedAfterEnd []string
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

// SetAttributes DROPS everything once the span has ended, because that is what a real span does.
//
// It used to accept attributes after End and keep them, and that made this double lie in the one
// way that mattered: TestTheSpanCarriesTheModelThatAnswered passed with the span closed before the
// body was decoded, which is precisely the bug moving the span's ownership out of send was meant to
// fix. Measured -- the mutation that restores the old shape passed against the old double and fails
// against this one. A test double that is more forgiving than production turns a guard into
// decoration.
//
// The dropped keys are kept so a test can say what was lost rather than only that something was.
func (s *recordingSpan) SetAttributes(attrs map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, v := range attrs {
		if s.ended {
			s.droppedAfterEnd = append(s.droppedAfterEnd, k)
			continue
		}
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

	client, err := New(Config{GatewayBaseURL: srv.URL,
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

	client, err := New(Config{GatewayBaseURL: srv.URL,
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
	// `{gen_ai.operation.name} {gen_ai.request.model}` -- the convention's rule, and the one Argus
	// asked for explicitly rather than a lower-cardinality departure from it.
	if span.name != "chat llama3-8b-q4" {
		t.Errorf("a span is named {operation} {model}; got %q", span.name)
	}
	if !span.ended {
		t.Error("the span was never ended")
	}
	// This is the whole point of the span: matching a caller's trace to the platform's.
	for key, want := range map[string]any{
		"gen_ai.provider.name":   "prometheus-gateway",
		"gen_ai.operation.name":  "chat",
		"gen_ai.request.model":   "llama3-8b-q4",
		"http.request.method":    "POST",
		"url.path":               "/v1/chat/completions",
		"prometheus.request_id":  "req-42",
		"prometheus.trace_id":    "trace-42",
		"prometheus.instance_id": "qwen3-0-6b-iq4-nl-local-1",
	} {
		if span.attrs[key] != want {
			t.Errorf("%s: got %v, want %v", key, span.attrs[key], want)
		}
	}
	// Deprecated in favour of gen_ai.provider.name. Asserted absent, not just un-asserted: an
	// emitter that sent both would pass every check above while still populating the attribute
	// Veritium asked us to stop sending.
	if _, found := span.attrs["gen_ai.system"]; found {
		t.Error("gen_ai.system is deprecated and must not be emitted alongside its replacement")
	}
	if len(span.errs) != 0 {
		t.Errorf("a successful call recorded errors: %v", span.errs)
	}
}

func TestAFailedCallIsRecordedOnTheSpanAndLogged(t *testing.T) {
	var buf bytes.Buffer
	tracer := &recordingTracer{}
	srv := observedServer(t, true)

	client, err := New(Config{GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s",
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
	ctx, span := client.startSpan(context.Background(), "POST", "/v1/chat/completions", "m")
	if ctx == nil || span == nil {
		t.Fatal("a no-op span must still be usable")
	}
	span.SetAttributes(map[string]any{"k": "v"})
	span.RecordError(errors.New("x"))
	span.End()
}

// A wait a caller would notice has to say so, or it arrives as a latency bug.
//
// The platform warned us about this shape directly: they were sent a report of "requests hanging
// 30-60 seconds with no clean load threshold". They were not hangs. Their Retry-After on a 429 is
// seconds until the window resets, so it runs 0-60, and an SDK that respects it -- as it should --
// looks from outside like one slow call among fast ones.

func TestALongRetryWaitIsReportedAtInfo(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelInfo}))

	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "contract", 300)
			return
		}
		calls++
		w.Header().Set("Content-Type", "application/problem+json")
		if calls == 1 {
			w.Header().Set("Retry-After", "2")
			w.WriteHeader(429)
			_, _ = w.Write([]byte(`{"type":"x/rate-limit-exceeded-requests","status":429}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
	}))
	defer srv.Close()

	client, err := New(Config{
		GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s", Logger: logger,
		Retry: &RetryPolicy{MaxAttempts: 3, MaxBackoff: 5 * time.Second},
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	if _, err := client.Models.Mine(context.Background()); err != nil {
		t.Fatalf("the retry should have succeeded: %v", err)
	}

	out := buf.String()
	if !strings.Contains(out, "waiting before a retry") {
		t.Errorf("a two-second wait left nothing at INFO to explain it:\n%s", out)
	}
	if !strings.Contains(out, "delay_s=2") {
		t.Errorf("the wait was not reported with its length:\n%s", out)
	}
}

func TestAShortBackoffStaysAtDebug(t *testing.T) {
	// The noise worry is frequent small retries, not the rare long one. A sub-second backoff that
	// shouted at INFO would train people to filter the level that matters.
	var buf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelInfo}))

	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "contract", 300)
			return
		}
		calls++
		if calls == 1 {
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(503)
			_, _ = w.Write([]byte(`{"type":"x/backend-unavailable","status":503}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
	}))
	defer srv.Close()

	client, err := New(Config{
		GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s", Logger: logger,
		Retry: &RetryPolicy{MaxAttempts: 3, InitialBackoff: 10 * time.Millisecond,
			MaxBackoff: 10 * time.Millisecond},
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	if _, err := client.Models.Mine(context.Background()); err != nil {
		t.Fatalf("the retry should have succeeded: %v", err)
	}

	if strings.Contains(buf.String(), "waiting before a retry") {
		t.Errorf("a 10ms backoff should not reach INFO:\n%s", buf.String())
	}
}

// The shared table is spec/otel-genai.json, and this asserts the map in observability.go against
// it. Python and Rust have the mirror of this test.
//
// AXO-139 is why this is a test and not a comment asking for care: five SDKs kept five hand-copied
// lists of rate-limit scopes, they diverged, the published TypeScript documented a value the header
// never sends, and nothing could catch it because the list was prose in a doc comment.
func TestOperationNamesMatchTheSharedTable(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(specDir(t), "otel-genai.json"))
	if err != nil {
		t.Fatalf("reading the shared table: %v", err)
	}

	var table struct {
		ProviderName string `json:"provider_name"`
		Operations   []struct {
			Path      string `json:"path"`
			Operation string `json:"operation"`
		} `json:"operations"`
		NoGenAI []struct {
			Path string `json:"path"`
		} `json:"no_genai_attributes"`
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatalf("parsing the shared table: %v", err)
	}
	if len(table.Operations) == 0 {
		t.Fatal("the table lists no operations, so this test would pass by comparing nothing")
	}

	if providerName != table.ProviderName {
		t.Errorf("gen_ai.provider.name: emitting %q, table says %q", providerName, table.ProviderName)
	}

	for _, entry := range table.Operations {
		if got := operationName(entry.Path); got != entry.Operation {
			t.Errorf("%s: gen_ai.operation.name is %q, table says %q", entry.Path, got, entry.Operation)
		}
	}

	// The excluded routes are the half a count would miss: a map answering every path would satisfy
	// the loop above and still put a catalog listing in GenAI aggregations as an inference call
	// that used no tokens.
	for _, entry := range table.NoGenAI {
		if got := operationName(entry.Path); got != "" {
			t.Errorf("%s carries no GenAI operation, but the map returns %q", entry.Path, got)
		}
	}
}

// The second witness: the model that ANSWERED, next to the one that was asked for.
//
// ARG-AXO-001. On the gateway's own span these two come from the same already-resolved variable, so
// the pair cannot differ -- Prometheus measured that and published it as P-21 rather than let Argus
// watch thirty minutes of data and conclude the case never happens. From a client they come from
// two places: the request model is the string the caller wrote, which may be an alias, and the
// response model is read out of the body. Argus asked for it because half the inference incidents
// they have seen are the model that answered not being the one asked for.
//
// This could not be emitted until the span's lifetime moved out of send and into doJSON: the body
// is decoded by the caller, so the span used to be closed by the time the served model was known.
func TestTheSpanCarriesTheModelThatAnswered(t *testing.T) {
	tracer := &recordingTracer{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"access_token":"t","token_type":"Bearer","expires_in":300}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		// The alias on the way in, the public name on the way out: the shape Prometheus measured
		// when they found their own span reporting both as the resolved name.
		_, _ = w.Write([]byte(`{"id":"c","object":"chat.completion","created":0,
			"model":"qwen3-0.6b",
			"choices":[{"index":0,"finish_reason":"stop",
			            "message":{"role":"assistant","content":"hi"}}],
			"usage":{"prompt_tokens":11,"completion_tokens":3,"total_tokens":14}}`))
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s", Tracer: tracer})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	_, err = client.Chat.Create(context.Background(), ChatRequest{
		Model:    "qwen3-0-6b-iq4-nl-local-1",
		Messages: []Message{TextMessage("user", "hi")},
	})
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	span := tracer.spans[0]
	for key, want := range map[string]any{
		"gen_ai.request.model":       "qwen3-0-6b-iq4-nl-local-1",
		"gen_ai.response.model":      "qwen3-0.6b",
		"gen_ai.usage.input_tokens":  int64(11),
		"gen_ai.usage.output_tokens": int64(3),
	} {
		if span.attrs[key] != want {
			t.Errorf("%s: got %v (%T), want %v", key, span.attrs[key], span.attrs[key], want)
		}
	}
	// The pair DIFFERING is the whole point, asserted as a pair rather than as two values: two
	// correct-looking values that happen to be equal is exactly the state the gateway's span was in
	// while reporting both.
	if span.attrs["gen_ai.request.model"] == span.attrs["gen_ai.response.model"] {
		t.Error("the pair is equal, so this test could not tell a resolved alias from a missing one")
	}
	// It has to be set BEFORE the span ends. A real span discards what arrives after End, and so
	// does this double -- which it did not until a mutation restoring the old span ownership passed
	// against it. That is the assertion, not span.ended on its own.
	if len(span.droppedAfterEnd) > 0 {
		t.Errorf("attributes were set after the span ended, so they are lost in production: %v",
			span.droppedAfterEnd)
	}
	if !span.ended {
		t.Error("the span never ended")
	}
}

// Absent, never zero. A fabricated 0 reads exactly like a measured 0 and sums into a trace total
// that looks exact. Argus raised it and confirmed their own usage() skips nil for the same reason.
func TestUsageIsAbsentRatherThanZeroWhenUnreported(t *testing.T) {
	tracer := &recordingTracer{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/oauth2/token" {
			_, _ = w.Write([]byte(`{"access_token":"t","token_type":"Bearer","expires_in":300}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":"c","object":"chat.completion","created":0,"model":"m",
			"choices":[{"index":0,"finish_reason":"stop",
			            "message":{"role":"assistant","content":"hi"}}]}`))
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s", Tracer: tracer})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	if _, err := client.Chat.Create(context.Background(), ChatRequest{
		Model: "m", Messages: []Message{TextMessage("user", "hi")},
	}); err != nil {
		t.Fatalf("create: %v", err)
	}

	span := tracer.spans[0]
	for _, key := range []string{"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"} {
		if _, present := span.attrs[key]; present {
			t.Errorf("%s was set with nothing to report: %v", key, span.attrs[key])
		}
	}
	// The served model is still there: the body named it, only the usage was missing.
	if span.attrs["gen_ai.response.model"] != "m" {
		t.Errorf("gen_ai.response.model: got %v", span.attrs["gen_ai.response.model"])
	}
}

// recordOutcome runs on the success path, so a body that is not what we expect must contribute
// nothing rather than panic. encoding/json gives every number as float64, so a fractional count is
// a body we do not understand -- reporting int64(11.5) as 11 would be inventing a measurement.
func TestRecordOutcomeSurvivesABodyItDoesNotUnderstand(t *testing.T) {
	for name, body := range map[string]map[string]any{
		"no model":            {},
		"model not a string":  {"model": 7.0},
		"model empty":         {"model": ""},
		"usage not an object": {"usage": "nope"},
		"count not a number":  {"model": "m", "usage": map[string]any{"prompt_tokens": "11"}},
		"count fractional":    {"model": "m", "usage": map[string]any{"prompt_tokens": 11.5}},
	} {
		t.Run(name, func(t *testing.T) {
			span := &recordingSpan{attrs: map[string]any{}}
			recordOutcome(span, body)
			if _, present := span.attrs["gen_ai.usage.input_tokens"]; present {
				t.Errorf("invented a token count from %v: %v", body, span.attrs)
			}
		})
	}
}
