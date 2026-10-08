module github.com/Root1V/axonium-sdk/go

go 1.22

// v0.6.3 was tagged with the Version constant still reading "0.6.2", so every request from it
// reports "axonium-go/0.6.2" as its User-Agent. The module proxy had already fetched the tag by the
// time the release gate reported it, and the proxy is immutable, so the version cannot be corrected
// in place -- only withdrawn. Nothing else about it is wrong; the code is identical to v0.6.4.
retract v0.6.3
