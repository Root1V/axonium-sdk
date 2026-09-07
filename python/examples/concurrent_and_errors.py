"""Concurrent requests, and what the typed errors let you do about failures.

    python examples/concurrent_and_errors.py MODEL_ID

One client is shared across all the tasks: the token is fetched once and refreshed once no matter
how many requests are in flight, and connections are pooled.
"""

from __future__ import annotations

import asyncio
import sys

from axonium import (
    AsyncAxonium,
    BackendUnavailableError,
    ConfigurationError,
    ContextExceededError,
    RateLimitError,
    RetryPolicy,
    SpendCapExceededError,
    TimeoutError,
)

QUESTIONS = [
    "What is the capital of France?",
    "What is the capital of Japan?",
    "What is the capital of Peru?",
]


async def ask(client: AsyncAxonium, model: str, question: str) -> str:
    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": question}],
            # Generous on purpose: a reasoning model spends its budget thinking before it emits
            # any content, so a tight limit yields an empty string rather than a short answer.
            max_tokens=512,
        )
    except RateLimitError as exc:
        # Already retried according to the policy; reaching here means the budget is still spent.
        return f"rate limited, retry in {exc.retry_after}s"
    except BackendUnavailableError as exc:
        # With retry_after set the gateway's circuit breaker is open and that value is its real
        # expected recovery time. Without it, the backend was simply unreachable.
        return f"backend unavailable{f', retry in {exc.retry_after}s' if exc.retry_after else ''}"
    except SpendCapExceededError:
        # Distinct from a rate limit on purpose: waiting will not fix this one.
        return "spend cap reached; the cap has to be raised"
    except ContextExceededError:
        return "prompt is too long for this model's context window"
    except TimeoutError:
        # Deliberately not retried: the backend may still be generating, and a retry would start
        # a second billable generation rather than resuming the first.
        return "timed out; the backend may still be working"

    return completion.content or "(no content)"


async def main(model: str) -> None:
    try:
        # Retrying a generation is never free, so the policy is explicit rather than implied.
        client = AsyncAxonium(retry=RetryPolicy(max_attempts=3))
    except ConfigurationError as exc:
        print(f"Not configured: {exc}")
        return

    async with client:
        answers = await asyncio.gather(*(ask(client, model, q) for q in QUESTIONS))

        for question, answer in zip(QUESTIONS, answers, strict=True):
            print(f"{question}\n  -> {answer}\n")

        if client.last_rate_limit:
            print(f"Requests left this window: {client.last_rate_limit.remaining_requests}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
