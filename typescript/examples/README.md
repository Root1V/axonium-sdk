# Examples

Every file here runs. `1`, `3`, `4` and `5` are scripts; `2` is a Next.js Route Handler, which needs a
Next app around it.

```bash
export AXONIUM_GATEWAY_BASE_URL=https://gateway.example
export AXONIUM_CLIENT_ID=… AXONIUM_CLIENT_SECRET=…

node 1-chat.ts          # Node 22+ runs TypeScript directly
node 3-vision.ts path/to/screenshot.png
node 4-tool-calling.ts
node 5-governed.ts
```

They pick models by **modality** from the live catalogue rather than hardcoding ids, because a fixed id
fails on every deployment that does not serve it — and that failure reads as a broken SDK instead of as
a different catalogue.
