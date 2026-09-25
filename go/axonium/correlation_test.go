package axonium

import "testing"

// An error whose body is not a full problem+json still has to carry its request id.
//
// Measured 2026-09-25 on POST /v1/models/{model}/predict, which forwards a backend's validation
// failure verbatim: the body is FastAPI's {"detail": [...]} with no request_id, while the header
// carried one all along. Reading the body alone handed the caller nothing to take to the platform
// team, on exactly the errors where they most need it.
func TestCorrelationFallsBackToTheHeaders(t *testing.T) {
	err := errorFromBody(422, map[string]any{"detail": "x"}, nil, nil, "req-from-header", "trace-from-header")

	if err.RequestID != "req-from-header" {
		t.Errorf("RequestID = %q, want the header's", err.RequestID)
	}
	if err.TraceID != "trace-from-header" {
		t.Errorf("TraceID = %q, want the header's", err.TraceID)
	}
}

func TestTheBodyWinsWhenItHasThem(t *testing.T) {
	// The gateway's own envelope echoes the header, so this changes nothing where the contract is
	// honoured -- the fallback only fires where the body fell short.
	body := map[string]any{"request_id": "from-body", "trace_id": "trace-body"}

	err := errorFromBody(400, body, nil, nil, "from-header", "trace-header")

	if err.RequestID != "from-body" || err.TraceID != "trace-body" {
		t.Errorf("got %q/%q, want the body's", err.RequestID, err.TraceID)
	}
}

func TestNeitherSourceLeavesItEmpty(t *testing.T) {
	err := errorFromBody(500, nil, nil, nil, "", "")

	if err.RequestID != "" || err.TraceID != "" {
		t.Errorf("invented %q/%q out of nothing", err.RequestID, err.TraceID)
	}
}
