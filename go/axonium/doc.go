// Package axonium is the Go SDK for the Prometheus Gateway inference API.
//
// This package is a placeholder. The Python SDK under python/ is being built first as the
// reference implementation; this package will be implemented against the same contract, and
// validated with the shared fixtures in spec/ so that both SDKs behave identically.
//
// Planned surface:
//
//	client, err := axonium.New(axonium.Config{
//		AuthBaseURL:    "...",
//		GatewayBaseURL: "...",
//		ClientID:       "...",
//		ClientSecret:   "...",
//	})
//	models, err := client.Models.List(ctx)
//	resp, err := client.Chat.Completions.Create(ctx, req)
//	stream, err := client.Chat.Completions.Stream(ctx, req)
//
// Implementation notes carried over from the specification:
//
//   - Token cache guarded by sync.RWMutex, refreshed lazily on the read path using expires_in
//     read from the token response — never a hardcoded TTL.
//   - SSE parsing needs a bufio.Scanner with a custom SplitFunc for blank-line-terminated
//     records; the default line scanner does not handle the SSE record format correctly.
//   - Per-call timeouts via context.Context deadlines, since the 600s non-streaming and 120s
//     streaming figures must be overridable per call rather than only globally.
//   - Errors are a typed hierarchy over the gateway's RFC 9457 problem-details envelope, so
//     callers branch on the error type rather than string-matching the human-readable detail.
package axonium
