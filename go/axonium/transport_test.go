package axonium

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"
)

// Retry-After is read straight into a sleep, so a hostile or broken value must never reach one.
// A negative wait is meaningless and an infinite one never returns.
func TestRetryAfterParsing(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		date string
		want *float64
	}{
		{raw: "30", want: ptr(30.0)},
		{raw: "0", want: ptr(0.0)},
		{raw: "-100", want: ptr(0.0)}, // clamped, not propagated
		{raw: "NaN", want: nil},       // discarded
		{raw: "Inf", want: nil},       // discarded
		{raw: "1e400", want: nil},     // overflows to +Inf
		{raw: "tomorrow", want: nil},  // not a number and not a date
		{raw: "", want: nil},          // absent
		// An HTTP-date is resolved against the server's own Date, so our clock cannot skew it.
		{raw: "Tue, 14 Nov 2023 22:13:50 GMT", date: "Tue, 14 Nov 2023 22:13:20 GMT", want: ptr(30.0)},
		// A date in the past clamps to zero rather than going negative.
		{raw: "Tue, 14 Nov 2023 22:13:00 GMT", date: "Tue, 14 Nov 2023 22:13:20 GMT", want: ptr(0.0)},
		// A date with nothing to measure against is unusable.
		{raw: "Tue, 14 Nov 2023 22:13:50 GMT", want: nil},
		{raw: "Tue, 14 Nov 2023 22:13:50 GMT", date: "not a date", want: nil},
	} {
		name := tc.raw
		if name == "" {
			name = "(absent)"
		}
		t.Run(name, func(t *testing.T) {
			h := http.Header{}
			if tc.raw != "" {
				h.Set("Retry-After", tc.raw)
			}
			if tc.date != "" {
				h.Set("Date", tc.date)
			}

			got := retryAfterSeconds(h)
			switch {
			case tc.want == nil && got != nil:
				t.Errorf("expected no wait, got %v", *got)
			case tc.want != nil && got == nil:
				t.Errorf("expected %v, got none", *tc.want)
			case tc.want != nil && *got != *tc.want:
				t.Errorf("got %v, want %v", *got, *tc.want)
			}
		})
	}
}

// A caller's own cancellation must come back as theirs, not be relabelled as a transport fault of
// ours -- otherwise it looks like the SDK failed when in fact the caller asked it to stop.
func TestTransportFailuresAreTranslatedHonestly(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if err := translateTransportError(cancelled, errors.New("whatever")); !errors.Is(err, context.Canceled) {
		t.Errorf("a cancelled caller should see their own cancellation, got %v", err)
	}

	expired, stop := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer stop()
	err := translateTransportError(expired, errors.New("whatever"))
	if !errors.Is(err, ErrTimeout) {
		t.Errorf("an expired deadline should be a timeout, got %v", err)
	}
	if !strings.Contains(err.Error(), "second billable generation") {
		t.Errorf("the timeout should explain why it is not retried, got %q", err)
	}

	plain := translateTransportError(context.Background(), errors.New("connection refused"))
	if !errors.Is(plain, ErrTransport) || errors.Is(plain, ErrTimeout) {
		t.Errorf("a connection failure is transport, not timeout: %v", plain)
	}
}

// Backoff doubles and is capped. With jitter off the schedule is exact, which is what makes it
// assertable at all.
func TestBackoffDoublesAndIsCapped(t *testing.T) {
	p := RetryPolicy{InitialBackoff: time.Second, MaxBackoff: 4 * time.Second}
	for attempt, want := range map[int]time.Duration{1: time.Second, 2: 2 * time.Second, 3: 4 * time.Second, 4: 4 * time.Second} {
		if got := p.backoff(attempt); got != want {
			t.Errorf("attempt %d: got %v, want %v", attempt, got, want)
		}
	}

	// With jitter the delay is spread below the nominal value, never above it.
	jittered := RetryPolicy{InitialBackoff: time.Second, MaxBackoff: 4 * time.Second, Jitter: true}
	for i := 0; i < 20; i++ {
		d := jittered.backoff(2)
		if d < time.Second || d > 2*time.Second {
			t.Fatalf("jittered delay %v outside the expected half-range", d)
		}
	}
}

// A cooldown only ever comes from a wait the platform supplied; the SDK never invents one.
func TestCooldownRegistryOnlyRecordsServerSuppliedWaits(t *testing.T) {
	r := newCooldownRegistry()

	r.record("k", 0)
	r.record("k", -time.Second)
	if got := r.remaining("k"); got != 0 {
		t.Errorf("a non-positive wait must not register, got %v", got)
	}

	r.record("k", time.Hour)
	if got := r.remaining("k"); got <= 0 {
		t.Errorf("a recorded wait should be pending, got %v", got)
	}

	// An expired cooldown clears itself on read rather than lingering.
	r.record("expired", time.Nanosecond)
	time.Sleep(time.Millisecond)
	if got := r.remaining("expired"); got != 0 {
		t.Errorf("an elapsed cooldown should be gone, got %v", got)
	}

	// note() only acts on a backend-unavailable carrying a wait.
	r2 := newCooldownRegistry()
	r2.note("m", &APIError{Status: 503, TypeSuffix: "backend-unavailable"})
	if r2.remaining("m") != 0 {
		t.Error("no Retry-After means no cooldown: the wait would be our invention")
	}
	r2.note("m", &APIError{Status: 429, TypeSuffix: "rate-limit-exceeded-requests", RetryAfter: ptr(60.0)})
	if r2.remaining("m") != 0 {
		t.Error("only backend-unavailable cools a backend down")
	}
	r2.note("m", &APIError{Status: 503, TypeSuffix: "backend-unavailable", RetryAfter: ptr(60.0)})
	if r2.remaining("m") <= 0 {
		t.Error("a server-supplied wait on backend-unavailable should register")
	}
}

func TestSleepForRespectsCancellation(t *testing.T) {
	if !sleepFor(context.Background(), time.Millisecond) {
		t.Error("an uninterrupted wait should complete")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if sleepFor(ctx, time.Hour) {
		t.Error("a cancelled context must cut the wait short")
	}
}
