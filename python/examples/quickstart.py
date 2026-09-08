"""Basic usage: discover what a token can call, then call it.

    python examples/quickstart.py

Reads configuration from the environment. If your credentials live in a .env file, load it before
constructing the client — the SDK does not read .env itself, and deliberately resolves settings
when the client is built rather than at import time, so load order cannot silently change what you
get.
"""

from __future__ import annotations

from axonium import Axonium, ConfigurationError, ForbiddenError, UnknownModelError


def main() -> None:
    try:
        client = Axonium()
    except ConfigurationError as exc:
        # Names the missing setting and the environment variable that supplies it.
        print(f"Not configured: {exc}")
        return

    with client:
        # Public: no token is fetched for this call.
        catalog = client.models.list()
        print(f"Deployed models: {', '.join(catalog.ids) or '(none)'}")

        # Filtered to what this token actually holds a model:<id> grant for. Worth doing once at
        # startup: it turns "why is everything 403" into a list you can read.
        mine = client.models.mine()
        print(f"Callable with this token: {', '.join(mine.ids) or '(none)'}")

        if not mine.ids:
            print("This token has no model grants; ask the operator to add model:<id> scopes.")
            return

        text_models = [m.id for m in mine if m.modality == "text"]
        if not text_models:
            print("This token has no text model to chat with.")
            return

        model = text_models[0]
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: pong"}],
                # Generous on purpose. A reasoning model spends its budget thinking before it
                # emits any content, so a small limit returns an empty string and
                # finish_reason="length" rather than a short answer.
                max_tokens=512,
            )
        except UnknownModelError:
            print(f"{model} is not registered on this gateway.")
            return
        except ForbiddenError as exc:
            # The SDK checks the denial against the scopes the token was granted and says what
            # is missing, rather than leaving you to guess.
            print(f"Denied: {exc}")
            return

        if completion.content:
            print(f"\n{model} says: {completion.content}")
        else:
            reason = completion.choices[0].finish_reason if completion.choices else None
            print(f"\n{model} returned no content (finish_reason={reason!r}).")
            if completion.reasoning:
                print(f"It was still reasoning: {completion.reasoning[:120]}...")
                print("Raise max_tokens to give it room to finish.")

        if completion.usage:
            print(f"Tokens: {completion.usage.total_tokens}")
        if completion.meta:
            print(f"Request ID: {completion.meta.request_id}")
        if client.last_rate_limit:
            print(f"Requests left this window: {client.last_rate_limit.remaining_requests}")


if __name__ == "__main__":
    main()
