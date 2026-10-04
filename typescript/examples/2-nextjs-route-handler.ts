/**
 * Streaming to the browser from a Next.js Route Handler. Drop this at `app/api/chat/route.ts`.
 *
 * **The browser never sees a credential and never talks to the gateway.** This handler runs on your
 * server, holds the credential, and forwards only text — which is the shape Apeiron's own security rule
 * requires and the one this package is built for.
 *
 * On the client:
 *
 * ```ts
 * const res = await fetch("/api/chat", { method: "POST", body: JSON.stringify({ prompt }) });
 * const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
 * for (;;) {
 *   const { done, value } = await reader.read();
 *   if (done) break;
 *   setText((t) => t + value);
 * }
 * ```
 */
import { Axonium, StreamInterruptedError } from "axonium";

// Module scope on purpose: one client per process reuses its token across requests, and the token
// manager coalesces concurrent refreshes. A client per request would fetch a token per request.
const api = new Axonium();

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const { prompt } = (await request.json()) as { prompt: string };

  let stream;
  try {
    stream = await api.chat.completions.stream(
      {
        model: "qwen3-0.6b",
        messages: [{ role: "user", content: prompt }],
        max_tokens: 800,
      },
      // The client disconnecting aborts the generation instead of paying for output nobody will read.
      { signal: request.signal },
    );
  } catch (error) {
    // A failure BEFORE the first byte can still be a status, so it gets one. Once the stream has begun
    // the headers are already sent and a failure can only arrive in band — which is the branch below.
    return Response.json(
      { error: error instanceof Error ? error.message : "unknown" },
      { status: 502 },
    );
  }

  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for await (const chunk of stream) {
          if (chunk.delta) controller.enqueue(encoder.encode(chunk.delta));
        }
        controller.close();
      } catch (error) {
        // The generation died mid-sentence. The text that did arrive is already on the wire, so the
        // honest thing is to append a marker rather than discard it — `error.partialContent` holds the
        // same text if you would rather re-render.
        if (error instanceof StreamInterruptedError) {
          controller.enqueue(encoder.encode("\n\n[la generación se interrumpió]"));
          controller.close();
        } else {
          controller.error(error);
        }
      }
    },
  });

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      // Or a proxy buffers the whole generation and the user waits for all of it at once.
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
