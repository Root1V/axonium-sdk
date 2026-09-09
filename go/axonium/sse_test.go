package axonium

import (
	"testing"
)

// The SSE layer decides what counts as an event, and its edge cases are the ones that turn a
// healthy stream into a broken one or hide a failure as a success.

func TestDecodeLineSkipsWhatIsNotAnEvent(t *testing.T) {
	for _, line := range []string{
		"",                    // record separator
		"   ",                 // whitespace only
		": this is a comment", // SSE comment
		"event: message",      // a field this protocol does not use
		"id: 42",              // likewise
		"data:",               // an empty data field
		"data: {not json",     // a single bad chunk must not destroy a good stream
		"data: [1,2,3]",       // valid JSON but not an object
	} {
		if _, ok := decodeLine(line); ok {
			t.Errorf("%q should not have produced an event", line)
		}
	}
}

func TestDecodeLineRecognizesTheSentinelAndPayloads(t *testing.T) {
	ev, ok := decodeLine("data: [DONE]")
	if !ok || !ev.done {
		t.Fatal("the terminal sentinel was not recognized")
	}

	ev, ok = decodeLine(`data: {"id":"c1"}`)
	if !ok || ev.done || ev.payload["id"] != "c1" {
		t.Fatalf("a payload line did not decode: %+v", ev)
	}

	// No space after the colon is equally valid SSE.
	if ev, ok = decodeLine(`data:{"id":"c2"}`); !ok || ev.payload["id"] != "c2" {
		t.Fatalf("data: without a following space must still decode: %+v", ev)
	}
}

// The in-band failure is detected by the presence of the error key, never by matching its message.
// The gateway documents one failure string today, but that is an implementation detail rather than
// a contract, and a string match would stop working the day a second one appears.
func TestInBandErrorIsDetectedByKeyNotMessage(t *testing.T) {
	for name, payload := range map[string]map[string]any{
		"documented string":   {"error": "stream interrupted"},
		"undocumented string": {"error": "something else entirely"},
		"structured object":   {"error": map[string]any{"code": 500, "message": "backend died"}},
	} {
		t.Run(name, func(t *testing.T) {
			var acc accumulator
			acc.content.WriteString("partial")

			_, err := acc.feed(sseEvent{payload: payload}, ResponseMeta{RequestID: "req-9"})
			if err == nil {
				t.Fatal("an error key must interrupt the stream whatever its shape")
			}
			streamErr, ok := err.(*StreamError)
			if !ok {
				t.Fatalf("expected a *StreamError, got %T", err)
			}
			if streamErr.PartialContent != "partial" {
				t.Errorf("what was already received must survive: got %q", streamErr.PartialContent)
			}
			if streamErr.RequestID != "req-9" {
				t.Errorf("the correlation ID must reach the caller: got %q", streamErr.RequestID)
			}
		})
	}
}

// Usage derived from timings is flagged, because billing on an estimate believing it exact does not
// fail -- it just quietly does not add up.
func TestUsageFromTimingsIsFlaggedEstimated(t *testing.T) {
	var acc accumulator
	acc.timings = map[string]any{"prompt_n": 10.0, "cache_n": 5.0, "predicted_n": 20.0}

	usage := acc.finalUsage()
	if usage == nil {
		t.Fatal("timings should have produced usage")
	}
	if *usage.PromptTokens != 15 {
		t.Errorf("prompt tokens must include the cached prefix: got %d, want 15", *usage.PromptTokens)
	}
	if *usage.CompletionTokens != 20 || *usage.TotalTokens != 35 {
		t.Errorf("derived counts are wrong: %+v", usage)
	}
	if !usage.Estimated {
		t.Error("a derived figure must be marked estimated")
	}
}

// A reported usage always wins over a derived one, and is not marked estimated.
func TestReportedUsageWinsOverTimings(t *testing.T) {
	reported := 5
	var acc accumulator
	acc.usage = &Usage{PromptTokens: &reported}
	acc.timings = map[string]any{"prompt_n": 999.0}

	usage := acc.finalUsage()
	if *usage.PromptTokens != 5 || usage.Estimated {
		t.Errorf("a reported usage must win and stay unflagged: %+v", usage)
	}
}

// Empty timings must not manufacture a zero-token usage: "nobody measured" is not "it was free".
func TestEmptyTimingsProduceNoUsage(t *testing.T) {
	var acc accumulator
	if acc.finalUsage() != nil {
		t.Error("no timings and no usage must produce no usage at all")
	}

	acc.timings = map[string]any{"prompt_n": 0.0, "predicted_n": 0.0}
	if acc.finalUsage() != nil {
		t.Error("all-zero timings are a backend that reported nothing, not a free generation")
	}
}
