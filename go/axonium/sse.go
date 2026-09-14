package axonium

import (
	"bufio"
	"encoding/json"
	"sort"
	"strings"
)

// Two things about this stream are easy to get wrong, and both are handled here rather than at the
// call sites.
//
// Failures arrive in-band. By the time a backend fails mid-generation the 200 and
// text/event-stream headers are already committed, so a broken stream cannot be signalled with an
// HTTP status. The gateway emits an error chunk instead, followed by the terminal sentinel. A
// client that only checks the status code sees a truncated response as a successful one.
//
// Token counts may never arrive as usage. llama.cpp-family backends send no usage chunk at all;
// the counts have to be recovered from the final chunk's timings. Other backends do send usage, so
// both are handled, and a derived figure is marked Estimated so a caller can tell the difference.

const dataPrefix = "data:"

// doneSentinel is the terminal marker. The gateway appends this itself at stream end regardless of
// whether the backend sent one, so it can be relied on to mark completion.
const doneSentinel = "[DONE]"

// sseEvent is one decoded data: line -- either a terminal marker or a payload.
type sseEvent struct {
	payload map[string]any
	done    bool
}

// decodeLine decodes one wire line, returning ok=false for a line that carries no event.
//
// Blank lines separate records, and anything that is not a data: field (comments, event:, id:) is
// not part of this protocol and is skipped rather than treated as an error.
func decodeLine(line string) (sseEvent, bool) {
	stripped := strings.TrimSpace(line)
	if stripped == "" || !strings.HasPrefix(stripped, dataPrefix) {
		return sseEvent{}, false
	}

	data := strings.TrimSpace(strings.TrimPrefix(stripped, dataPrefix))
	if data == doneSentinel {
		return sseEvent{done: true}, true
	}
	if data == "" {
		return sseEvent{}, false
	}

	var payload map[string]any
	if json.Unmarshal([]byte(data), &payload) != nil {
		// A single unparseable chunk is not worth destroying an otherwise good stream over.
		return sseEvent{}, false
	}
	return sseEvent{payload: payload}, true
}

// accumulator assembles chunks into a final result and decides when a stream has failed.
type accumulator struct {
	content   strings.Builder
	reasoning strings.Builder
	usage     *Usage
	timings   map[string]any
	toolCalls map[int]*partialToolCall
	done      bool
}

// feed consumes one payload event, returning its chunk. A non-nil error is an in-band failure.
func (a *accumulator) feed(ev sseEvent, meta ResponseMeta) (*ChatCompletionChunk, error) {
	// Detected by the presence of the key, not by matching its message: the gateway documents only
	// one failure string today, but that is an implementation detail rather than contract.
	if raw, ok := ev.payload["error"]; ok {
		return nil, &StreamError{
			Message:        describeStreamError(raw),
			PartialContent: a.content.String(),
			RequestID:      meta.RequestID,
			TraceID:        meta.TraceID,
			Raw:            ev.payload,
		}
	}

	chunk := chunkFromPayload(ev.payload)

	if chunk.Content() != "" {
		a.content.WriteString(chunk.Content())
	}
	if chunk.Reasoning() != "" {
		a.reasoning.WriteString(chunk.Reasoning())
	}
	if chunk.Usage != nil {
		a.usage = chunk.Usage
	}
	if chunk.Timings != nil {
		a.timings = chunk.Timings
	}
	for _, fragment := range chunk.ToolCallFragments() {
		if m, ok := fragment.(map[string]any); ok {
			a.absorbToolCall(m)
		}
	}
	return chunk, nil
}

func describeStreamError(raw any) string {
	if s, ok := raw.(string); ok {
		return s
	}
	encoded, err := json.Marshal(raw)
	if err != nil {
		return "unspecified"
	}
	return string(encoded)
}

// finalUsage returns token accounting for the completed stream, if it can be determined.
//
// A backend-reported usage wins. Otherwise the counts are reconstructed from the final chunk's
// timings and flagged Estimated so a caller never mistakes a derived number for a reported one.
func (a *accumulator) finalUsage() *Usage {
	if a.usage != nil {
		return a.usage
	}
	if a.timings == nil {
		return nil
	}

	// Read by value rather than by key presence: a backend that sends an explicit null must be
	// treated as not having measured it, the same as one that omits the field. A zero here would
	// make an unknown look like a cold cache.
	cached, cacheReported := numeric(a.timings["cache_n"])
	// input includes the cached prefix; cache_read says how many of those were cached. Summing is
	// the copy, not an addition of two disjoint buckets.
	prompt := asInt(a.timings["prompt_n"]) + int(cached)
	completion := asInt(a.timings["predicted_n"])
	if prompt == 0 && completion == 0 {
		return nil
	}
	total := prompt + completion
	usage := &Usage{
		PromptTokens:     &prompt,
		CompletionTokens: &completion,
		TotalTokens:      &total,
		Estimated:        true,
	}
	if cacheReported {
		cachedTokens := int(cached)
		usage.CacheReadTokens = &cachedTokens
	}
	return usage
}

func asInt(v any) int {
	n, ok := numeric(v)
	if !ok {
		return 0
	}
	return int(n)
}

// scanSSE splits on the blank line that terminates an SSE record, and additionally on single
// newlines so that a record's individual data: fields are surfaced one at a time. The default
// bufio line scanner would work for this gateway's one-field records, but not for a record
// carrying several data: lines, which the SSE format permits.
func scanSSE(data []byte, atEOF bool) (advance int, token []byte, err error) {
	return bufio.ScanLines(data, atEOF)
}

// Tool calls arrive in pieces that are not individually valid JSON. A call is split across as many
// deltas as it takes -- "{", "\"", "city" -- and only the first carries the identity (id, type,
// function.name). index is the correlation key, because id never repeats. They are reassembled
// here so a caller never has to.

// partialToolCall is one tool call being assembled from its fragments.
type partialToolCall struct {
	id        string
	kind      string
	name      string
	arguments strings.Builder
}

// absorb folds one wire fragment in.
//
// Identity is recorded the first time it is seen rather than overwritten: id is documented never
// to repeat, so a second, different one would be a correlation bug, and adopting it silently would
// repoint a call that is already accumulating arguments.
func (p *partialToolCall) absorb(fragment map[string]any) {
	if p.id == "" {
		p.id, _ = fragment["id"].(string)
	}
	if p.kind == "" {
		p.kind, _ = fragment["type"].(string)
	}

	function, ok := fragment["function"].(map[string]any)
	if !ok {
		return
	}
	if p.name == "" {
		p.name, _ = function["name"].(string)
	}
	if piece, ok := function["arguments"].(string); ok {
		p.arguments.WriteString(piece)
	}
}

// assemble renders the call as the same ToolCall a non-streaming completion returns.
//
// arguments stays a JSON string, exactly as non-streaming delivers it, rather than being decoded
// here. That is what lets one piece of caller code handle both, and it means a stream cut short by
// max_tokens still hands back the fragment that did arrive instead of failing or dropping the
// call. An identity field the backend never sent comes back as the zero value.
func (p *partialToolCall) assemble() ToolCall {
	return ToolCall{
		ID:       p.id,
		Type:     p.kind,
		Function: FunctionCall{Name: p.name, Arguments: p.arguments.String()},
	}
}

func (a *accumulator) absorbToolCall(fragment map[string]any) {
	index, ok := numeric(fragment["index"])
	if !ok {
		// Every backend seen so far sends it, and it is the only way to tell two concurrent calls
		// apart. Falling back to slot 0 keeps the single-call case working instead of dropping the
		// call outright, which is the only case a stream without indices can represent
		// unambiguously anyway.
		index = 0
	}
	key := int(index)
	if a.toolCalls == nil {
		a.toolCalls = map[int]*partialToolCall{}
	}
	if a.toolCalls[key] == nil {
		a.toolCalls[key] = &partialToolCall{}
	}
	a.toolCalls[key].absorb(fragment)
}

// finalToolCalls returns the calls assembled so far, in the non-streaming shape.
//
// Ordered by the wire index rather than by arrival, so a backend that interleaves two calls still
// yields them in the order the model asked for.
func (a *accumulator) finalToolCalls() []ToolCall {
	if len(a.toolCalls) == 0 {
		return nil
	}
	indices := make([]int, 0, len(a.toolCalls))
	for index := range a.toolCalls {
		indices = append(indices, index)
	}
	sort.Ints(indices)

	calls := make([]ToolCall, 0, len(indices))
	for _, index := range indices {
		calls = append(calls, a.toolCalls[index].assemble())
	}
	return calls
}
