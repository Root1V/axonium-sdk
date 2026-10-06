package axonium

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strings"
)

// Response bodies for the inference endpoints are passed through from heterogeneous backends
// verbatim, so the types here keep the decoded body in Raw rather than discarding what they do not
// model: a field this SDK does not know about is still reachable by the caller.

// Message is one turn of a conversation.
type Message struct {
	Role    string `json:"role"`
	Content any    `json:"content,omitempty"`

	// ReasoningContent is a reasoning model's chain of thought, kept apart from Content because it
	// is not part of the answer. Never inferred from Content and never merged into it.
	ReasoningContent string `json:"reasoning_content,omitempty"`

	// ToolCalls are complete calls. On a streamed delta this stays empty and the fragments are
	// read from the chunk's Raw instead -- see (*ChatCompletionChunk).ToolCallFragments.
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	Name       string     `json:"name,omitempty"`
}

// TextMessage builds a plain text message.
func TextMessage(role, content string) Message {
	return Message{Role: role, Content: content}
}

var validRoles = map[string]bool{"system": true, "user": true, "assistant": true, "tool": true}

// ChatRequest is a chat completion request.
//
// The gateway's request schema is an allowlist and silently discards fields it does not accept, so
// this type models only what the gateway reads. Extra carries anything else a caller wants to send
// -- deliberately explicit, so an unrecognized field is a decision rather than a typo that
// vanishes without trace.
type ChatRequest struct {
	Model    string    `json:"model"`
	Messages []Message `json:"messages"`

	Temperature *float64 `json:"temperature,omitempty"`
	TopP        *float64 `json:"top_p,omitempty"`
	MaxTokens   *int     `json:"max_tokens,omitempty"`
	Stop        []string `json:"stop,omitempty"`
	Seed        *int     `json:"seed,omitempty"`
	Tools       []any    `json:"tools,omitempty"`
	ToolChoice  any      `json:"tool_choice,omitempty"`

	// ResponseFormat requests structured output. Forwarded verbatim, like Tools: the grammar is
	// the engine's, and validating the schema here would be a second copy of its rules that
	// drifts.
	//
	// The answer arrives as a JSON STRING in the message content, not as a nested object -- parse
	// it yourself. This SDK deliberately does not, for the same reason tool-call Arguments stays a
	// string: a generation stopped by MaxTokens leaves it truncated.
	ResponseFormat any `json:"response_format,omitempty"`

	// ChatTemplateKwargs are the variables llama.cpp hands to the model's own chat template,
	// forwarded as an opaque mapping (PRM-195).
	//
	// THE KEYS BELONG TO EACH MODEL'S TEMPLATE, not to the gateway, so nothing validates them here
	// and the useful set differs per model: enable_thinking for the Qwen3.6 family, reasoning_effort
	// for gpt-oss. A key the template does not read is ignored by the template, silently, and
	// nothing can tell you that -- check the model card.
	//
	// It is how a reasoning model's thinking is turned off, and that is not a micro-optimisation:
	// measured on the platform, the same question answered in 215 tokens and 6.91 s without it and
	// 16 tokens and 0.71 s with {"enable_thinking": false}.
	//
	// reasoning_effort goes INSIDE this mapping. At the top level it does nothing at all, and the
	// platform keeps it outside the accepted set on purpose so it keeps showing up in
	// X-Prometheus-Ignored-Parameters instead of being quietly accepted and quietly dropped.
	ChatTemplateKwargs map[string]any `json:"chat_template_kwargs,omitempty"`

	// Logprobs asks for the chosen token's own probability, under Choice.Logprobs (PRM-187). The
	// point is an agent deciding when to escalate to a person rather than act on a guess.
	Logprobs *bool `json:"logprobs,omitempty"`
	// TopLogprobs adds the N most likely alternatives at each position, 0 to 20.
	//
	// REQUIRES Logprobs set to true. Sending it alone is a 422 the gateway raises before the
	// engine sees it; validate() refuses it here, because the round trip buys nothing.
	TopLogprobs *int `json:"top_logprobs,omitempty"`

	Extra map[string]any `json:"-"`

	// Instance pins this request to one instance, by label ("#2") or by full instance id. It is
	// sent as a header, never in Model: a grant covers a model, billing attributes to a model, and
	// the catalog lists models.
	//
	// Intended for reproducing a problem or comparing two machines, not for normal traffic: a pin
	// opts out of load balancing AND of failover. An unavailable pinned instance returns
	// ErrBackendUnavailable rather than quietly going elsewhere, and a name that does not serve
	// this model returns ErrUnknownInstance. The pin is kept across retries -- dropping it would
	// answer a different question than the caller asked.
	Instance string `json:"-"`

	// IdempotencyKey makes a retry safe: a repeat with the same key and the same body returns the
	// stored result for 24 hours, without reaching a model, recording usage, or counting against
	// the spend cap. It is also what lets this SDK retry a client-side timeout at all -- without a
	// key that retry would be a second billable generation, so it is not attempted.
	//
	// Reuse a key only to retry the identical request. Reusing it for a different one, on a
	// different endpoint, or while the first is still in flight is ErrIdempotencyConflict.
	//
	// Works on Stream too, with one boundary worth knowing: a key replays a stream the gateway
	// FINISHED and whose delivery the caller's connection dropped, never one the model itself
	// broke -- that needs resuming rather than replaying, which nobody has built. Within that
	// boundary a streamed replay is as reliable as a non-streaming one, including with no wait
	// between calls. Read Meta().IdempotentReplay to know which you got.
	//
	// What happens to a key whose request FAILED is not specified, and is the gateway's decision
	// rather than this SDK's. A consumer with derived keys found a failed step returning its stored
	// error for the whole window; we could not reproduce it with the failures we can produce. Do
	// not assume a failure frees the key.
	IdempotencyKey string `json:"-"`
}

func (r *ChatRequest) validate() error {
	var problems []string

	if strings.TrimSpace(r.Model) == "" {
		problems = append(problems, "model is required")
	}
	if len(r.Messages) == 0 {
		problems = append(problems, "messages must not be empty")
	}
	for i, m := range r.Messages {
		if !validRoles[m.Role] {
			problems = append(problems, fmt.Sprintf("messages[%d].role %q is not one of system, user, assistant, tool", i, m.Role))
		}
		if err := rejectRemoteImages(m.Content); err != nil {
			problems = append(problems, fmt.Sprintf("messages[%d]: %v", i, err))
		}
	}
	if r.Temperature != nil && (*r.Temperature < 0 || *r.Temperature > 2) {
		problems = append(problems, "temperature must be within [0, 2]")
	}
	if r.TopP != nil && (*r.TopP <= 0 || *r.TopP > 1) {
		problems = append(problems, "top_p must be within (0, 1]")
	}
	if r.MaxTokens != nil && *r.MaxTokens <= 0 {
		problems = append(problems, "max_tokens must be greater than zero")
	}
	// The rule is the engine's -- llama.cpp answers "top_logprobs requires logprobs to be set to
	// true" -- and the gateway enforces it before forwarding so the refusal arrives as problem+json.
	// Refused here as well: the call site is a better place to learn it than a round trip is.
	if r.TopLogprobs != nil && (r.Logprobs == nil || !*r.Logprobs) {
		problems = append(problems, "top_logprobs requires logprobs to be true")
	}
	if r.TopLogprobs != nil && (*r.TopLogprobs < 0 || *r.TopLogprobs > 20) {
		problems = append(problems, "top_logprobs must be within [0, 20]")
	}

	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalidRequest, strings.Join(problems, "; "))
	}
	return nil
}

// rejectRemoteImages refuses a remote image URL before it is sent.
//
// The gateway will not fetch one: accepting a caller-supplied URL server-side is a
// server-side-request-forgery vector, so images must be inlined as data URIs. Saying so here costs
// nothing, where letting it through costs a round trip and returns an error whose reason is not
// obvious from the message.
func rejectRemoteImages(content any) error {
	parts, ok := content.([]any)
	if !ok {
		return nil
	}
	for _, part := range parts {
		m, ok := part.(map[string]any)
		if !ok {
			continue
		}
		image, ok := m["image_url"].(map[string]any)
		if !ok {
			continue
		}
		url := stringOr(image["url"])
		if strings.HasPrefix(url, "http://") || strings.HasPrefix(url, "https://") {
			return fmt.Errorf("image_url.url must be a data: URI, not a remote URL; the gateway does not fetch remote images, because doing so server-side would be an SSRF vector")
		}
	}
	return nil
}

// MarshalJSON merges Extra into the encoded object.
func (r ChatRequest) MarshalJSON() ([]byte, error) {
	type plain ChatRequest
	encoded, err := json.Marshal(plain(r))
	if err != nil {
		return nil, err
	}
	if len(r.Extra) == 0 {
		return encoded, nil
	}

	var merged map[string]any
	if err := json.Unmarshal(encoded, &merged); err != nil {
		return nil, err
	}
	for k, v := range r.Extra {
		merged[k] = v
	}
	return json.Marshal(merged)
}

// FunctionCall is the function a tool call names, and the arguments it was called with.
type FunctionCall struct {
	Name string `json:"name,omitempty"`

	// Arguments are the arguments as the model produced them: a JSON string, not a decoded
	// object. Kept raw on purpose. Streaming delivers this in fragments that are only valid once
	// concatenated, and a generation cut short by max_tokens leaves a string that was never going
	// to parse -- decoding here would turn that into an error surfaced from inside a response
	// type, for a caller who only wanted to see what the model had managed to say. Use
	// ParseArguments when you want the object.
	Arguments string `json:"arguments,omitempty"`
}

// ToolCall is a tool call, in the one shape both streaming and non-streaming produce.
//
// Streamed calls arrive split across fragments that are individually invalid JSON; the SDK
// reassembles them into exactly this, so the same caller code handles both.
type ToolCall struct {
	ID string `json:"id,omitempty"`
	// Type is "function" for everything the gateway forwards today. Modelled rather than assumed,
	// because the field exists on the wire precisely so it can grow.
	Type     string       `json:"type,omitempty"`
	Function FunctionCall `json:"function"`
}

// ParseArguments decodes Function.Arguments into an object.
//
// The returned error wraps ErrToolCallArguments when the string is not valid JSON. The usual cause
// is a generation that ran out of tokens mid-call, so check FinishReason before calling this on a
// response you have not verified completed. The raw string is left untouched either way.
func (t ToolCall) ParseArguments() (map[string]any, error) {
	var parsed map[string]any
	if err := json.Unmarshal([]byte(t.Function.Arguments), &parsed); err != nil {
		return nil, fmt.Errorf(
			"%w: tool call %q has arguments that are not a JSON object (%v). A generation stopped "+
				"by max_tokens leaves them truncated -- check FinishReason. Raw value: %q",
			ErrToolCallArguments, t.ID, err, t.Function.Arguments,
		)
	}
	return parsed, nil
}

// Choice is one completion candidate.
type Choice struct {
	Index        int      `json:"index"`
	Message      *Message `json:"message,omitempty"`
	Delta        *Message `json:"delta,omitempty"`
	FinishReason string   `json:"finish_reason,omitempty"`
	// Logprobs is present only when the request asked for it, and only on an engine that has it.
	// On one that does not, the response simply carries no logprobs key -- RequireParameters is
	// how to be told, rather than inferring it from the absence.
	Logprobs *ChoiceLogprobs `json:"logprobs,omitempty"`
}

// ChoiceLogprobs holds the per-token probabilities for one choice, in OpenAI's shape.
type ChoiceLogprobs struct {
	Content []TokenLogprob `json:"content,omitempty"`
}

// TokenLogprob is one generated token and how likely the model thought it was.
//
// Logprob is a NATURAL LOGARITHM, which is the part worth saying out loud: -0.00054 is about
// 99.95% and -7.6 is about 0.05%. Read as a probability it looks like a number near zero meaning
// "unlikely", and the mistake is silent, so Probability exists.
type TokenLogprob struct {
	Token   string  `json:"token,omitempty"`
	Logprob float64 `json:"logprob"`
	// TopLogprobs are the alternatives at this position, best first. Empty unless asked for.
	TopLogprobs []TokenLogprob `json:"top_logprobs,omitempty"`
}

// Probability is exp(Logprob).
func (t TokenLogprob) Probability() float64 { return math.Exp(t.Logprob) }

// ChatCompletion is a non-streaming chat completion response.
type ChatCompletion struct {
	ID      string   `json:"id"`
	Object  string   `json:"object"`
	Created int64    `json:"created"`
	Model   string   `json:"model"`
	Choices []Choice `json:"choices"`
	Usage   *Usage   `json:"usage,omitempty"`

	// Raw is the decoded body as received, so backend-specific fields this SDK does not model stay
	// reachable rather than being dropped.
	Raw map[string]any `json:"-"`

	Meta ResponseMeta `json:"-"`
}

// Content returns the first choice's text, or "" when the model returned tool calls instead of
// prose, or when it is still in its reasoning phase.
func (c *ChatCompletion) Content() string {
	if len(c.Choices) == 0 || c.Choices[0].Message == nil {
		return ""
	}
	return contentText(c.Choices[0].Message.Content)
}

// Reasoning returns the first choice's chain of thought, separate from the answer.
func (c *ChatCompletion) Reasoning() string {
	if len(c.Choices) == 0 || c.Choices[0].Message == nil {
		return ""
	}
	return c.Choices[0].Message.ReasoningContent
}

// FinishReason returns why the first choice stopped.
func (c *ChatCompletion) FinishReason() string {
	if len(c.Choices) == 0 {
		return ""
	}
	return c.Choices[0].FinishReason
}

// ToolCalls returns the first choice's tool calls.
//
// The gateway does not interpret them, and neither does this SDK beyond giving them a shape:
// Arguments is still the model's own string, reachable raw.
func (c *ChatCompletion) ToolCalls() []ToolCall {
	if len(c.Choices) == 0 || c.Choices[0].Message == nil {
		return nil
	}
	return c.Choices[0].Message.ToolCalls
}

// ToolCallFragments returns this chunk's raw tool-call fragments, which are *not* usable on their
// own: a fragment carries a slice of an arguments string that is invalid JSON by itself, and only
// the first one for a given index carries the identity. Use the stream's ToolCalls for the
// assembled calls; this is here for a caller who wants to watch them arrive.
func (c *ChatCompletionChunk) ToolCallFragments() []any {
	// Read from Raw rather than from the decoded Delta. A fragment is not a ToolCall: it carries
	// `index`, which the complete shape has no field for and would therefore drop, and only a
	// slice of the arguments string. Decoding it into the typed struct would silently lose the one
	// field the reassembler correlates on.
	choices, ok := c.Raw["choices"].([]any)
	if !ok || len(choices) == 0 {
		return nil
	}
	choice, ok := choices[0].(map[string]any)
	if !ok {
		return nil
	}
	delta, ok := choice["delta"].(map[string]any)
	if !ok {
		return nil
	}
	fragments, _ := delta["tool_calls"].([]any)
	return fragments
}

// ChatCompletionChunk is one streamed delta.
type ChatCompletionChunk struct {
	ID      string   `json:"id"`
	Object  string   `json:"object"`
	Created int64    `json:"created"`
	Model   string   `json:"model"`
	Choices []Choice `json:"choices"`
	Usage   *Usage   `json:"usage,omitempty"`

	// Timings is a llama.cpp-family backend's timing block, present only on a final chunk. It is
	// the only source of token counts when the backend sends no usage chunk.
	Timings map[string]any `json:"timings,omitempty"`

	Raw map[string]any `json:"-"`
}

// Content returns this chunk's text delta, which is empty while the model is reasoning.
func (c *ChatCompletionChunk) Content() string {
	if len(c.Choices) == 0 || c.Choices[0].Delta == nil {
		return ""
	}
	return contentText(c.Choices[0].Delta.Content)
}

// Reasoning returns this chunk's chain-of-thought delta. A chunk carries one or the other, so a
// progress display can show which phase the model is in.
func (c *ChatCompletionChunk) Reasoning() string {
	if len(c.Choices) == 0 || c.Choices[0].Delta == nil {
		return ""
	}
	return c.Choices[0].Delta.ReasoningContent
}

// FinishReason returns why this chunk's choice stopped, empty until the final chunk.
func (c *ChatCompletionChunk) FinishReason() string {
	if len(c.Choices) == 0 {
		return ""
	}
	return c.Choices[0].FinishReason
}

func chunkFromPayload(payload map[string]any) *ChatCompletionChunk {
	chunk := &ChatCompletionChunk{Raw: payload}
	if encoded, err := json.Marshal(payload); err == nil {
		_ = json.Unmarshal(encoded, chunk)
	}
	return chunk
}

// contentText flattens the content field, which is a string for plain messages and a list of parts
// for multimodal ones.
func contentText(content any) string {
	switch v := content.(type) {
	case string:
		return v
	case []any:
		var b strings.Builder
		for _, part := range v {
			m, ok := part.(map[string]any)
			if !ok {
				continue
			}
			if text, ok := m["text"].(string); ok {
				b.WriteString(text)
			}
		}
		return b.String()
	}
	return ""
}

// ChatService is the chat completions API.
type ChatService struct{ client *Client }

// Create sends a non-streaming chat completion.
func (s *ChatService) Create(ctx context.Context, req ChatRequest) (*ChatCompletion, error) {
	if err := req.validate(); err != nil {
		return nil, err
	}
	if err := s.client.checkModality(ctx, req.Model, modalitiesChat); err != nil {
		return nil, err
	}

	var raw map[string]any
	meta, err := s.client.doJSON(ctx, http.MethodPost, "/v1/chat/completions", req, &raw, req.Model, req.Instance, req.IdempotencyKey)
	if err != nil {
		return nil, err
	}

	out := &ChatCompletion{Raw: raw, Meta: meta}
	encoded, err := json.Marshal(raw)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrTransport, err)
	}
	if err := json.Unmarshal(encoded, out); err != nil {
		return nil, &APIError{
			Status: http.StatusOK, RequestID: meta.RequestID, TraceID: meta.TraceID,
			Detail: fmt.Sprintf("the gateway returned a chat completion this SDK could not parse: %v", err),
		}
	}
	out.Raw, out.Meta = raw, meta
	return out, nil
}

// Stream opens a streaming chat completion.
//
// Streaming is a separate method rather than a flag on Create: it needs its own scope
// (inference:stream, which inference:read does not imply), it fails in two different ways, and its
// result is a different type. Folding it into Create would hide all three.
//
// A rejection that arrives INSTEAD of the stream is retried like any other request, Retry-After
// included: the gateway reads the engine's status before the 200/text/event-stream headers exist,
// so nothing was generated and nothing was billed, and reopening is a first generation rather than
// a second. A stream that has already BEGUN is never retried -- see ChatCompletionStream.
//
// The returned stream must be closed. Closing cancels the request, which propagates through the
// gateway to Prometheus and stops the generation.
func (s *ChatService) Stream(ctx context.Context, req ChatRequest) (*ChatCompletionStream, error) {
	if err := req.validate(); err != nil {
		return nil, err
	}
	if err := s.client.checkModality(ctx, req.Model, modalitiesChat); err != nil {
		return nil, err
	}

	payload := map[string]any{}
	encoded, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidRequest, err)
	}
	if err := json.Unmarshal(encoded, &payload); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidRequest, err)
	}
	payload["stream"] = true

	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidRequest, err)
	}

	// The stream owns this cancel for its whole life, so Close can tear the connection down.
	streamCtx, cancel := context.WithCancel(ctx)
	resp, meta, err := s.client.send(streamCtx, http.MethodPost, "/v1/chat/completions", body, true, req.Model, req.Instance, req.IdempotencyKey)
	if err != nil {
		cancel()
		return nil, err
	}
	return newChatCompletionStream(resp, meta, cancel), nil
}
