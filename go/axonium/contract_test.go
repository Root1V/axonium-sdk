package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

// The contract manifest in spec/cases/manifest.json is the source of truth shared by every
// language SDK. Python, Go and Rust replay the same cases against the same recorded wire bytes, so
// identical behavior is enforced by construction rather than by parallel hand-written suites that
// drift apart.
//
// A case that passes here does not prove the gateway behaves this way -- today's fixtures are
// authored from the integration guide rather than recorded from a live deployment, so they pin the
// SDKs to each other. Re-recording them against a real deployment is tracked as AXO-47.

type manifest struct {
	Version int            `json:"version"`
	Cases   []contractCase `json:"cases"`
}

type contractCase struct {
	ID        string         `json:"id"`
	Operation string         `json:"operation"`
	Request   map[string]any `json:"request"`
	Response  struct {
		Status   int               `json:"status"`
		Headers  map[string]string `json:"headers"`
		BodyFile string            `json:"body_file"`
		SSEFile  string            `json:"sse_file"`
	} `json:"response"`
	Expect struct {
		Kind            string         `json:"kind"`
		Fields          map[string]any `json:"fields"`
		Content         *string        `json:"content"`
		Chunks          *int           `json:"chunks"`
		Usage           map[string]any `json:"usage"`
		PartialContent  *string        `json:"partial_content"`
		ErrorTypeSuffix string         `json:"error_type_suffix"`
	} `json:"expect"`
}

func specDir(t *testing.T) string {
	t.Helper()
	dir, err := filepath.Abs(filepath.Join("..", "..", "spec"))
	if err != nil {
		t.Fatalf("resolving the spec directory: %v", err)
	}
	return dir
}

func TestContractManifest(t *testing.T) {
	spec := specDir(t)

	raw, err := os.ReadFile(filepath.Join(spec, "cases", "manifest.json"))
	if err != nil {
		t.Fatalf("reading the manifest: %v", err)
	}
	var m manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("parsing the manifest: %v", err)
	}
	if len(m.Cases) == 0 {
		t.Fatal("the manifest declares no cases; a runner that asserts nothing would pass forever")
	}

	for _, c := range m.Cases {
		t.Run(c.ID, func(t *testing.T) { runContractCase(t, spec, c) })
	}
}

func runContractCase(t *testing.T, spec string, c contractCase) {
	fixture := c.Response.BodyFile
	if fixture == "" {
		fixture = c.Response.SSEFile
	}
	body, err := os.ReadFile(filepath.Join(spec, "fixtures", fixture))
	if err != nil {
		t.Fatalf("reading fixture %s: %v", fixture, err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "contract", 300)
			return
		}
		for k, v := range c.Response.Headers {
			w.Header().Set(k, v)
		}
		if c.Response.SSEFile != "" {
			w.Header().Set("Content-Type", "text/event-stream")
		} else {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(c.Response.Status)
		// Written verbatim: the .sse fixtures are literal wire captures, blank-line record
		// separators included, and reformatting them would test a stream this gateway never sends.
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)

	switch c.Expect.Kind {
	case "ok":
		result := invokeUnary(t, client, c)
		for path, want := range c.Expect.Fields {
			got := resolvePath(t, result, path)
			if !equalJSON(got, want) {
				t.Errorf("%s: got %#v, want %#v", path, got, want)
			}
		}
	case "stream", "stream_error":
		runStreamCase(t, client, c)
	default:
		t.Fatalf("unknown expectation kind %q", c.Expect.Kind)
	}
}

// invokeUnary dispatches a non-streaming operation and returns its result as generic JSON, so the
// manifest's dotted field paths resolve the same way they do in the Python runner.
func invokeUnary(t *testing.T, client *Client, c contractCase) map[string]any {
	t.Helper()
	ctx := context.Background()

	var (
		value any
		meta  ResponseMeta
		err   error
	)

	switch c.Operation {
	case "chat.completions.create":
		var out *ChatCompletion
		out, err = client.Chat.Create(ctx, chatRequestFrom(c.Request))
		if out != nil {
			value, meta = out, out.Meta
		}
	case "models.list":
		var out *ModelList
		out, err = client.Models.List(ctx)
		if out != nil {
			value, meta = out, out.Meta
		}
	case "models.mine":
		var out *ModelList
		out, err = client.Models.Mine(ctx)
		if out != nil {
			value, meta = out, out.Meta
		}
	case "embeddings.create":
		var out *EmbeddingList
		out, err = client.Embeddings.Create(ctx, embeddingRequestFrom(c.Request))
		if out != nil {
			value, meta = out, out.Meta
		}
	case "images.generate":
		var out *ImageList
		out, err = client.Images.Generate(ctx, imageRequestFrom(c.Request))
		if out != nil {
			value, meta = out, out.Meta
		}
	default:
		t.Fatalf("unsupported operation %q", c.Operation)
	}

	if err != nil {
		t.Fatalf("%s: %v", c.Operation, err)
	}

	generic := toGeneric(t, value)
	// meta is transport-level rather than part of the resource, so it is grafted on for path
	// resolution instead of being a JSON field on the type.
	generic["meta"] = toGeneric(t, metaAsMap(meta))

	// The manifest resolves a path against the SDK's own accessors where it exposes them, so a
	// case asserting "content" is checking what a caller would actually read rather than where the
	// value happens to sit in the payload. Python satisfies this with properties; Go grafts the
	// accessor results on here.
	if completion, ok := value.(*ChatCompletion); ok {
		generic["content"] = nilIfEmpty(completion.Content())
		generic["reasoning"] = nilIfEmpty(completion.Reasoning())
		if calls := completion.ToolCalls(); calls != nil {
			generic["tool_calls"] = calls
		}
	}
	return generic
}

func runStreamCase(t *testing.T, client *Client, c contractCase) {
	stream, err := client.Chat.Stream(context.Background(), chatRequestFrom(c.Request))
	if err != nil {
		t.Fatalf("opening the stream: %v", err)
	}
	defer stream.Close()

	chunks := 0
	for stream.Next() {
		chunks++
	}

	if c.Expect.Kind == "stream_error" {
		if !errors.Is(stream.Err(), ErrStreamInterrupted) {
			t.Fatalf("expected an in-band stream interruption, got %v", stream.Err())
		}
		var streamErr *StreamError
		if errors.As(stream.Err(), &streamErr) && c.Expect.PartialContent != nil {
			if streamErr.PartialContent != *c.Expect.PartialContent {
				t.Errorf("partial_content: got %q, want %q", streamErr.PartialContent, *c.Expect.PartialContent)
			}
		}
		return
	}

	if stream.Err() != nil {
		t.Fatalf("the stream failed: %v", stream.Err())
	}
	if c.Expect.Chunks != nil && chunks != *c.Expect.Chunks {
		t.Errorf("chunks: got %d, want %d", chunks, *c.Expect.Chunks)
	}
	if c.Expect.Content != nil && stream.Content() != *c.Expect.Content {
		t.Errorf("content: got %q, want %q", stream.Content(), *c.Expect.Content)
	}

	usage := stream.Usage()
	if c.Expect.Usage == nil {
		if usage != nil {
			t.Errorf("usage: got %+v, want none", usage)
		}
		return
	}
	if usage == nil {
		t.Fatalf("usage: got none, want %v", c.Expect.Usage)
	}
	for key, want := range c.Expect.Usage {
		var got any
		switch key {
		case "prompt_tokens":
			got = derefInt(usage.PromptTokens)
		case "completion_tokens":
			got = derefInt(usage.CompletionTokens)
		case "total_tokens":
			got = derefInt(usage.TotalTokens)
		case "estimated":
			got = usage.Estimated
		default:
			t.Fatalf("the manifest asserts an unknown usage key %q", key)
		}
		if !equalJSON(got, want) {
			t.Errorf("usage.%s: got %#v, want %#v", key, got, want)
		}
	}
}

// nilIfEmpty distinguishes "the model returned no prose" from "the model returned an empty
// string". The manifest asserts content is null for a tool-call response, and reporting "" there
// would claim the model answered with nothing rather than that it answered with calls.
func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func derefInt(v *int) any {
	if v == nil {
		return nil
	}
	return *v
}

func chatRequestFrom(raw map[string]any) ChatRequest {
	req := ChatRequest{Model: stringOr(raw["model"])}
	if messages, ok := raw["messages"].([]any); ok {
		for _, m := range messages {
			entry, ok := m.(map[string]any)
			if !ok {
				continue
			}
			req.Messages = append(req.Messages, Message{
				Role:    stringOr(entry["role"]),
				Content: entry["content"],
			})
		}
	}
	return req
}

func embeddingRequestFrom(raw map[string]any) EmbeddingRequest {
	req := EmbeddingRequest{Model: stringOr(raw["model"])}
	switch input := raw["input"].(type) {
	case string:
		req.Input = []string{input}
	case []any:
		for _, v := range input {
			req.Input = append(req.Input, stringOr(v))
		}
	}
	return req
}

func imageRequestFrom(raw map[string]any) ImageRequest {
	req := ImageRequest{Model: stringOr(raw["model"]), Prompt: stringOr(raw["prompt"]), Size: stringOr(raw["size"])}
	if n, ok := numeric(raw["n"]); ok {
		req.N = int(n)
	}
	return req
}

func metaAsMap(m ResponseMeta) map[string]any {
	out := map[string]any{"request_id": m.RequestID, "trace_id": m.TraceID}
	if m.RateLimit != nil {
		out["rate_limit"] = map[string]any{
			"limit_requests":     derefInt(m.RateLimit.LimitRequests),
			"remaining_requests": derefInt(m.RateLimit.RemainingRequests),
			"reset_requests":     derefInt(m.RateLimit.ResetRequests),
			"limit_tokens":       derefInt(m.RateLimit.LimitTokens),
			"remaining_tokens":   derefInt(m.RateLimit.RemainingTokens),
			"reset_tokens":       derefInt(m.RateLimit.ResetTokens),
		}
	}
	return out
}

// toGeneric re-encodes a typed value as generic JSON. For types carrying a Raw body it uses that
// instead, so a field the Go structs do not model is still visible to a manifest assertion --
// which is the point of keeping Raw at all.
func toGeneric(t *testing.T, value any) map[string]any {
	t.Helper()

	switch v := value.(type) {
	case *ChatCompletion:
		if v.Raw != nil {
			return v.Raw
		}
	case *ModelList:
		if v.Raw != nil {
			return v.Raw
		}
	case *EmbeddingList:
		if v.Raw != nil {
			return v.Raw
		}
	case *ImageList:
		if v.Raw != nil {
			return v.Raw
		}
	case map[string]any:
		return v
	}

	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("re-encoding the result: %v", err)
	}
	var generic map[string]any
	if err := json.Unmarshal(encoded, &generic); err != nil {
		t.Fatalf("decoding the result: %v", err)
	}
	return generic
}

// resolvePath walks a dotted path, with integer segments indexing into lists, matching the
// manifest's documented field_paths convention.
func resolvePath(t *testing.T, root any, path string) any {
	t.Helper()
	current := root
	for _, segment := range strings.Split(path, ".") {
		switch node := current.(type) {
		case map[string]any:
			value, ok := node[segment]
			if !ok {
				return nil
			}
			current = value
		case []any:
			index, err := strconv.Atoi(segment)
			if err != nil || index < 0 || index >= len(node) {
				return nil
			}
			current = node[index]
		default:
			return nil
		}
	}
	return current
}

// equalJSON compares after normalizing through JSON, so an int from a Go struct and a float64 from
// the manifest are not reported as a difference of type rather than of value.
func equalJSON(got, want any) bool {
	normalize := func(v any) any {
		encoded, err := json.Marshal(v)
		if err != nil {
			return fmt.Sprintf("%v", v)
		}
		var out any
		if json.Unmarshal(encoded, &out) != nil {
			return string(encoded)
		}
		return out
	}
	return reflect.DeepEqual(normalize(got), normalize(want))
}
