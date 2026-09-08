"""Streaming a completion, and handling a stream that fails partway.

    python examples/streaming.py MODEL_ID

Streaming needs the ``inference:stream`` scope, which is separate from the ``inference:read``
scope non-streaming calls use — holding one does not grant the other.
"""

from __future__ import annotations

import sys

from axonium import Axonium, ConfigurationError, StreamInterruptedError


def main(model: str) -> None:
    try:
        client = Axonium()
    except ConfigurationError as exc:
        print(f"Not configured: {exc}")
        return

    messages = [{"role": "user", "content": "Count from one to ten, in words."}]

    with client, client.chat.completions.stream(model=model, messages=messages) as stream:
        thinking = False
        try:
            for chunk in stream:
                if chunk.reasoning:
                    # A reasoning model streams its thinking before any answer token. Showing it
                    # is what keeps the first seconds from looking like a hang.
                    if not thinking:
                        print("[thinking] ", end="", flush=True)
                        thinking = True
                    print(chunk.reasoning, end="", flush=True)
                    continue

                if thinking:
                    print("\n\n[answer] ", end="", flush=True)
                    thinking = False
                print(chunk.content or "", end="", flush=True)
        except StreamInterruptedError as exc:
            # A mid-stream failure arrives in-band on a 200 whose headers were already sent, so
            # the HTTP status says nothing. Whatever arrived first is still usable.
            print(f"\n\nStream failed: {exc}")
            print(f"Received before the failure: {exc.partial_content!r}")
            return

        print("\n")

        usage = stream.usage()
        if usage is None:
            print("The backend reported no token counts.")
        elif usage.estimated:
            # llama.cpp-family backends send no usage chunk while streaming, so the counts are
            # reconstructed from the final chunk's timings.
            print(f"Tokens (estimated from timings): {usage.total_tokens}")
        else:
            print(f"Tokens: {usage.total_tokens}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
