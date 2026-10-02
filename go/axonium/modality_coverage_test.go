package axonium

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// endpointSets is the same list knownModalities is built from, named here so a set added to one and
// not the other is caught rather than inferred.
var endpointSets = map[string][]string{
	"modalitiesChat":       modalitiesChat,
	"modalitiesEmbeddings": modalitiesEmbeddings,
	"modalitiesImages":     modalitiesImages,
	"modalitiesRerank":     modalitiesRerank,
	"modalitiesPredict":    modalitiesPredict,
}

// Every checkModality call must pass one of the named endpoint sets.
//
// This is the test that was missing. RerankService.Create passed a literal []string{"rerank"}, and
// because knownModalities was a separate hand-kept map that had never heard of "rerank",
// checkModality returned before it could compare anything -- the call refused nothing, including the
// pairing its own doc comment promised to catch. A literal at the call site is how a modality gets
// accepted by an endpoint without ever becoming known, so literals are refused here.
func TestEveryModalityCheckUsesADeclaredSet(t *testing.T) {
	call := regexp.MustCompile(`checkModality\(ctx,\s*[^,]+,\s*([^)]+)\)`)

	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}

	found := 0
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		source, err := os.ReadFile(filepath.Clean(name))
		if err != nil {
			t.Fatal(err)
		}
		for _, match := range call.FindAllStringSubmatch(string(source), -1) {
			argument := strings.TrimSpace(match[1])
			found++
			if _, ok := endpointSets[argument]; !ok {
				t.Errorf("%s passes %q to checkModality; it must pass one of the declared endpoint "+
					"sets, because knownModalities is derived from those and a literal here is "+
					"accepted by an endpoint without ever becoming known", name, argument)
			}
		}
	}
	// The definition in catalog.go is not a call, so the count is call sites only.
	if found == 0 {
		t.Fatal("found no checkModality call sites; the pattern stopped matching, not the code")
	}
	t.Logf("%d call sites, all using a declared set", found)
}

// A modality only one endpoint accepts is still known, or that endpoint has no check at all.
func TestEveryAcceptedModalityIsKnown(t *testing.T) {
	for name, set := range endpointSets {
		for _, modality := range set {
			if !knownModalities[modality] {
				t.Errorf("%s accepts %q, which knownModalities does not contain: checkModality "+
					"returns early on it, so that endpoint refuses nothing", name, modality)
			}
		}
	}
	// And the reverse: a known modality belonging to no endpoint would be dead weight that makes
	// the derived set look broader than the checks it backs.
	for modality := range knownModalities {
		placed := false
		for _, set := range endpointSets {
			for _, m := range set {
				if m == modality {
					placed = true
				}
			}
		}
		if !placed {
			t.Errorf("knownModalities contains %q, which no endpoint accepts", modality)
		}
	}
}
