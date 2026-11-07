"""Control policies: stand-ins for the model that do nothing useful, to calibrate the tasks.

A task that a control passes measures little. Each control answers relay requests through
the same checks as `python -m evals.relay_answer`, like `evals.scripted_responder`:

- `always-ask`: every agent turn calls AskShopper with the same generic question and never
  searches (a forced structured-output call gets the shopper's message with no filters and
  `wants_products` false).
- `always-refuse`: every agent turn answers with a polite refusal that also says the item
  was not found, with no tool call.
- `first-tool-echo`: the first agent call of a turn calls the domain's most obvious search
  tool once with the shopper's raw message (SearchCatalog, SearchCocktails, SearchBooks,
  PlanEvening); once a tool result is in the conversation it lists up to five item names
  from it, in bold, with no prices, ratings, alcohol status or other facts.

    uv run python -m evals.controls --policy always-refuse --queue evals/relay_queue

Their scores say how much of a task set a model can pass without being useful: report them
next to a model's (docs/agent-eval.md#controls).
"""

import argparse
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from evals.relay import DEFAULT_QUEUE, RelayQueue
from evals.relay_answer import _text, forced_tool, tools_of
from evals.scripted_responder import answer_pending

Answer = tuple[str | None, list[dict]]

ASK_QUESTION = "Happy to help! Could you tell me a little more about what you're looking for?"
ASK_OPTIONS = ["Something casual", "Something special", "Surprise me"]
REFUSAL = "Sorry, I can't help with that here. I couldn't find that in the catalog."
# The most obvious search tool of each domain, in the order they are tried.
SEARCH_TOOLS = ("SearchCatalog", "SearchCocktails", "SearchBooks", "PlanEvening")
# Item names in tool results: "[id 3] Name | ...", "product #12 Name | ...", "book #7 ...".
ITEM_NAME = re.compile(r"(?:\[id \d+\]|\b(?:product|drink|book) #\d+)\s+([^|\n]+?)\s*\|")
MAX_NAMES = 5


def last_user_message(request: dict) -> str:
    for message in reversed(request.get("messages") or []):
        if message.get("role") == "user":
            return _text(message.get("content")).strip()
    return ""


def _has_tool_result(request: dict) -> bool:
    return any(m.get("role") == "tool" for m in request.get("messages") or [])


def _fit(schema: dict, field: str, value: str) -> str:
    """The value cut to the field's maxLength in the tool's JSON schema."""
    limit = ((schema.get("properties") or {}).get(field) or {}).get("maxLength")
    value = " ".join(value.split()) or "something"
    return value[:limit] if limit else value


def _forced_answer(request: dict, *, wants_products: bool) -> Answer:
    """A forced structured-output call (the fashion intent): the message, no filters."""
    name = forced_tool(request)
    assert name is not None
    schema = tools_of(request).get(name, {}).get("parameters") or {}
    properties = schema.get("properties") or {}
    message = last_user_message(request)
    args: dict = {}
    for field in schema.get("required") or []:
        args[field] = _fit(schema, field, message)
    if "query" in properties:
        args["query"] = _fit(schema, "query", message)
    if "wants_products" in properties:
        args["wants_products"] = wants_products
    return None, [{"name": name, "arguments": args}]


def always_ask(request: dict) -> Answer:
    if forced_tool(request):
        return _forced_answer(request, wants_products=False)
    if "AskShopper" in tools_of(request):
        args = {"question": ASK_QUESTION, "options": ASK_OPTIONS}
        return None, [{"name": "AskShopper", "arguments": args}]
    return ASK_QUESTION, []


def always_refuse(request: dict) -> Answer:
    if forced_tool(request):
        return _forced_answer(request, wants_products=False)
    return REFUSAL, []


def first_tool_echo(request: dict) -> Answer:
    if forced_tool(request):
        return _forced_answer(request, wants_products=True)
    if _has_tool_result(request):
        return list_names(request), []
    tools = tools_of(request)
    name = next((tool for tool in SEARCH_TOOLS if tool in tools), None)
    if name is None:
        return REFUSAL, []
    schema = tools[name]["parameters"]
    field = "query" if "query" in (schema.get("properties") or {}) else "occasion"
    message = last_user_message(request)
    return None, [{"name": name, "arguments": {field: _fit(schema, field, message)}}]


def list_names(request: dict) -> str:
    """Up to five item names from this turn's tool results, without any fact about them."""
    names: list[str] = []
    for message in request.get("messages") or []:
        if message.get("role") != "tool":
            continue
        for match in ITEM_NAME.finditer(_text(message.get("content"))):
            name = match.group(1).strip()
            if name and name not in names:
                names.append(name)
    if not names:
        return "The catalog search returned nothing for that."
    shown = ", ".join(f"**{name}**" for name in names[:MAX_NAMES])
    return f"Here is what the catalog search returned: {shown}."


POLICIES: dict[str, Callable[[dict], Answer]] = {
    "always-ask": always_ask,
    "always-refuse": always_refuse,
    "first-tool-echo": first_tool_echo,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", choices=sorted(POLICIES), required=True)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--poll", type=float, default=0.2)
    args = parser.parse_args(argv)
    queue = RelayQueue(args.queue)
    policy = POLICIES[args.policy]
    print(f"Answering relay requests in {args.queue.resolve()} as {args.policy}", flush=True)
    try:
        while True:
            answer_pending(queue, policy)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
