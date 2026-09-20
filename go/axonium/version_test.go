package axonium

import (
	"strings"
	"testing"
)

// The version guard in scripts/check_go_version.sh exists because Version reaches the platform.
// This pins that premise: if the User-Agent ever stops being built from the constant, the guard is
// still green while protecting nothing.
func TestTheUserAgentCarriesTheVersionConstant(t *testing.T) {
	if !strings.HasSuffix(userAgent, Version) {
		t.Fatalf("userAgent = %q, which does not end in Version = %q", userAgent, Version)
	}
	if !strings.HasPrefix(userAgent, "axonium-go/") {
		t.Errorf("userAgent = %q, want it to identify this SDK", userAgent)
	}
}
