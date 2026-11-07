"""A scripted stand-in for the model that answers relay requests, for dry runs and tests.

It answers each pending request with the deterministic rules of the end-to-end test model
(`tests/e2e/llm_stub.py`) and writes the answer through the same checks as
`python -m evals.relay_answer`, so a dry run exercises the relay, the CLI's validation, the
runner and the scorer end to end. Its scores mean nothing about any real model.

    uv run python -m evals.scripted_responder --queue evals/relay_queue
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from evals.relay import DEFAULT_QUEUE, RelayQueue
from evals.relay_answer import AnswerError, answer


def scripted_answer(request: dict) -> tuple[str | None, list[dict]]:
    """The e2e stub's reply to an OpenAI-style request: text and tool calls."""
    from tests.e2e.llm_stub import Script

    decided = Script().reply(request)
    calls = [
        {
            "id": call.get("id"),
            "name": call["function"]["name"],
            "arguments": json.loads(call["function"]["arguments"] or "{}"),
        }
        for call in decided.get("tool_calls") or []
    ]
    return decided.get("content"), calls


def answer_pending(
    queue: RelayQueue,
    answerer: Callable[[dict], tuple[str | None, list[dict]]] = scripted_answer,
) -> int:
    """Answer every pending request once with `answerer` (the scripted model by default,
    or a control policy from `evals.controls`); return how many were answered."""
    answered = 0
    for seq in queue.pending():
        text, calls = answerer(queue.read_request(seq))
        try:
            answer(queue, seq, text, calls)
        except AnswerError as error:
            # The script broke a schema: answer with plain text so the run goes on, and say so.
            print(f"{seq}: scripted answer rejected ({error}); answering with text", flush=True)
            answer(queue, seq, "I can't help with that right now.", [])
        answered += 1
    return answered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--poll", type=float, default=0.2)
    args = parser.parse_args(argv)
    queue = RelayQueue(args.queue)
    print(f"Answering relay requests in {args.queue.resolve()} with the scripted model", flush=True)
    try:
        while True:
            answer_pending(queue)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
