package axonium

import (
	"os"
	"path/filepath"
	"regexp"
	"testing"
)

// The Version constant has to match the tag that publishes it, and until v0.6.3 nothing local
// checked that.
//
// The release workflow does check it -- and it caught the mismatch -- but it runs ON the tag, which
// is after the module proxy has fetched it. Measured: the proxy served
// github.com/Root1V/axonium-sdk/go@v0.6.3 within thirty seconds, with the commit hash, while the
// workflow was still failing. The proxy is immutable, so there was nothing left to correct; v0.6.3
// is retracted in go.mod and v0.6.4 carries the same code.
//
// Go has no manifest, so the version is a constant and the tag is the other copy of it -- one truth
// in two places, with no local check. TypeScript has the same shape and a test for it
// (package.json versus src/version.ts), which caught the same mistake in the same hour, before
// anything was published. This is that test for Go: the CHANGELOG's newest Go entry is the third
// copy and the one a release is written against, so agreeing with it is what can be verified here.
func TestVersionMatchesTheNewestChangelogEntry(t *testing.T) {
	path := filepath.Join("..", "..", "CHANGELOG.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading the changelog: %v", err)
	}

	// The Go section, up to the next language heading.
	section := regexp.MustCompile(`(?s)\n## Go\n(.*?)\n## `).FindStringSubmatch(string(raw))
	if section == nil {
		t.Fatal("no ## Go section in CHANGELOG.md, so this test would pass by reading nothing")
	}

	// The first released heading after Unreleased. Anchored to a digit so "### Unreleased" is
	// skipped without naming it.
	found := regexp.MustCompile(`### (\d+\.\d+\.\d+)`).FindStringSubmatch(section[1])
	if found == nil {
		t.Fatal("the Go section has no released version heading")
	}

	if found[1] != Version {
		t.Fatalf("Version = %q but the newest Go CHANGELOG entry is %q.\n\n"+
			"Whichever is wrong, fix it HERE. The release workflow checks the constant against the\n"+
			"tag, and by then the module proxy has the tag and the proxy is immutable: v0.6.3 was\n"+
			"lost to exactly this and had to be retracted.", Version, found[1])
	}
}
