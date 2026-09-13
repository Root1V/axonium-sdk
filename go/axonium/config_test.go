package axonium

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A misconfigured deployment must fail at construction, naming both the field and the variable
// that can supply it -- not later, as a confusing request error.

func TestConfigurationErrorsNameTheFieldAndTheVariable(t *testing.T) {
	for _, v := range []string{"AXONIUM_AUTH_BASE_URL", "AXONIUM_GATEWAY_BASE_URL"} {
		t.Setenv(v, "")
		_ = os.Unsetenv(v)
	}

	for _, tc := range []struct {
		name    string
		cfg     Config
		mustSay []string
	}{
		{"no auth url", Config{GatewayBaseURL: "https://g.example", ClientID: "i", ClientSecret: "s"},
			[]string{"AuthBaseURL", "AXONIUM_AUTH_BASE_URL"}},
		{"no gateway url", Config{AuthBaseURL: "https://a.example", ClientID: "i", ClientSecret: "s"},
			[]string{"GatewayBaseURL", "AXONIUM_GATEWAY_BASE_URL"}},
		{"scheme-less url", Config{AuthBaseURL: "a.example", GatewayBaseURL: "https://g.example", ClientID: "i", ClientSecret: "s"},
			[]string{"http://", "https://"}},
		{"ratio out of range", Config{AuthBaseURL: "https://a.example", GatewayBaseURL: "https://g.example",
			ClientID: "i", ClientSecret: "s", RefreshAheadRatio: 1.5}, []string{"RefreshAheadRatio"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := New(tc.cfg)
			if !errors.Is(err, ErrConfiguration) {
				t.Fatalf("expected a configuration error, got %v", err)
			}
			for _, want := range tc.mustSay {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("the message should mention %q, got %q", want, err)
				}
			}
		})
	}
}

func TestTrailingSlashesAreNormalised(t *testing.T) {
	client, err := New(Config{
		AuthBaseURL: "https://a.example///", GatewayBaseURL: "https://g.example/",
		ClientID: "i", ClientSecret: "s",
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	cfg := client.Config()
	if cfg.AuthBaseURL != "https://a.example" || cfg.GatewayBaseURL != "https://g.example" {
		t.Errorf("got %q and %q", cfg.AuthBaseURL, cfg.GatewayBaseURL)
	}
}

func TestScopesSplitOnWhitespace(t *testing.T) {
	cfg := Config{Scope: "inference:read  model:qwen3-0.6b"}
	if got := cfg.Scopes(); len(got) != 2 || got[1] != "model:qwen3-0.6b" {
		t.Errorf("got %v", got)
	}
	if got := (&Config{}).Scopes(); got != nil {
		t.Errorf("no scope requested should yield nothing, got %v", got)
	}
}

func TestEnvironmentFillsUnsetFields(t *testing.T) {
	t.Setenv("AXONIUM_AUTH_BASE_URL", "https://env-auth.example")
	t.Setenv("AXONIUM_GATEWAY_BASE_URL", "https://env-gateway.example")
	t.Setenv("AXONIUM_CLIENT_ID", "env-id")
	t.Setenv("AXONIUM_CLIENT_SECRET", "env-secret")
	t.Setenv("AXONIUM_SCOPE", "inference:read")
	t.Setenv("AXONIUM_VERIFY_MODALITY", "true")

	client, err := New(Config{})
	if err != nil {
		t.Fatalf("building from the environment alone: %v", err)
	}
	defer client.Close()

	cfg := client.Config()
	if cfg.AuthBaseURL != "https://env-auth.example" || cfg.ClientID != "env-id" {
		t.Errorf("environment not read: %+v", cfg)
	}
	if !cfg.VerifyModality {
		t.Error("AXONIUM_VERIFY_MODALITY=true was not honoured")
	}
	if cfg.Timeouts.Request != DefaultTimeouts().Request {
		t.Error("unset timeouts should fall back to the defaults")
	}
}

// A deployment behind a self-signed certificate supplies its own trust. A bundle that cannot be
// read or contains nothing usable must fail at construction, not on the first request.
func TestCABundleIsValidatedAtConstruction(t *testing.T) {
	_, err := New(Config{
		AuthBaseURL: "https://a.example", GatewayBaseURL: "https://g.example",
		ClientID: "i", ClientSecret: "s", CABundle: filepath.Join(t.TempDir(), "missing.pem"),
	})
	if !errors.Is(err, ErrConfiguration) || !strings.Contains(err.Error(), "could not read") {
		t.Fatalf("a missing bundle should fail at construction, got %v", err)
	}

	empty := filepath.Join(t.TempDir(), "empty.pem")
	if err := os.WriteFile(empty, []byte("not a certificate"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err = New(Config{
		AuthBaseURL: "https://a.example", GatewayBaseURL: "https://g.example",
		ClientID: "i", ClientSecret: "s", CABundle: empty,
	})
	if !errors.Is(err, ErrConfiguration) || !strings.Contains(err.Error(), "no usable certificates") {
		t.Fatalf("a bundle with no certificates should say so, got %v", err)
	}
}

func TestPartialTimeoutsKeepTheOtherDefaults(t *testing.T) {
	client, err := New(Config{
		AuthBaseURL: "https://a.example", GatewayBaseURL: "https://g.example",
		ClientID: "i", ClientSecret: "s",
		Timeouts: Timeouts{Request: 5 * time.Second},
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	got := client.Config().Timeouts
	if got.Request != 5*time.Second {
		t.Errorf("the override was lost: %v", got.Request)
	}
	if got.Auth != DefaultTimeouts().Auth || got.Connect != DefaultTimeouts().Connect {
		t.Errorf("overriding one phase must not zero the others: %+v", got)
	}
}
