package axonium

import (
	"context"
	"encoding/json"
	"fmt"
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

	ToolCalls  []any  `json:"tool_calls,omitempty"`
	ToolCallID string `json:"tool_call_id,omitempty"`
	Name       string `json:"name,omitempty"`
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

	Extra map[string]any `json:"-"`
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

// Choice is one completion candidate.
type Choice struct {
	Index        int      `json:"index"`
	Message      *Message `json:"message,omitempty"`
	Delta        *Message `json:"delta,omitempty"`
	FinishReason string   `json:"finish_reason,omitempty"`
}

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

// ToolCalls returns the first choice's tool calls, passed through untouched.
func (c *ChatCompletion) ToolCalls() []any {
	if len(c.Choices) == 0 || c.Choices[0].Message == nil {
		return nil
	}
	return c.Choices[0].Message.ToolCalls
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
	meta, err := s.client.doJSON(ctx, http.MethodPost, "/v1/chat/completions", req, &raw)
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
// (inference:stream, which inference:read does not imply), it is never retried automatically, and
// its result is a different type. Folding it into Create would hide all three.
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
	resp, meta, err := s.client.send(streamCtx, http.MethodPost, "/v1/chat/completions", body, true)
	if err != nil {
		cancel()
		return nil, err
	}
	return newChatCompletionStream(resp, meta, cancel), nil
}
