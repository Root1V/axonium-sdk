package axonium

import (
	"bufio"
	"encoding/json"
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

	prompt := asInt(a.timings["prompt_n"]) + asInt(a.timings["cache_n"])
	completion := asInt(a.timings["predicted_n"])
	if prompt == 0 && completion == 0 {
		return nil
	}
	total := prompt + completion
	return &Usage{
		PromptTokens:     &prompt,
		CompletionTokens: &completion,
		TotalTokens:      &total,
		Estimated:        true,
	}
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
