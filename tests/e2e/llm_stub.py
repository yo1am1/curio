"""A scripted, OpenAI-compatible chat completions server for end-to-end runs.

The app talks to it exactly as it talks to a real provider (LLM_PROVIDER=openai and
LLM_BASE_URL pointing here), so the whole production path runs: langchain-openai, HTTP,
tool-call parsing, the agent engine, guardrails, the API and the UI. Replies follow
deterministic rules keyed on the shopper's words, which makes every flow reproducible:

- the intent extractor (forced SearchIntent tool) gets keywords, colours, audience, budget;
- the fashion and cocktail agents get tool calls for searches, related pieces, pantry,
  clarifying questions and approval proposals, then answers built only from tool results;
- the phrase "best deal" first gets an answer with an invented price, so the grounding
  check and self-correction can be seen end to end.

Run standalone: `python -m tests.e2e.llm_stub --port 8790`.
"""

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count

PRODUCT_LINE = re.compile(r"\[id (\d+)\] ([^|\n]+) \| [^|\n]+ \| [^|\n]+ \| ([^|\n]+) \| €([\d.]+)")
DRINK_LINE = re.compile(r"\[id (\d+)\] ([^|\n]+) \| ([^|\n]+) \|")
BUDGET = re.compile(r"(?:under|below|less than|up to|budget(?: is| of)?)\s*€?\s*(\d+)")
COLOURS = ("black", "white", "blue", "red", "beige", "grey", "pink", "green", "brown")
_ids = count(1)


def _text(content) -> str:
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{next(_ids)}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class Script:
    """Decides one reply from an OpenAI-style request body."""

    def reply(self, body: dict) -> dict:
        tools = {t["function"]["name"] for t in body.get("tools", [])}
        messages = body["messages"]
        if "SearchIntent" in tools:
            return {"tool_calls": [_tool_call("SearchIntent", self._intent(messages))]}
        if "SearchCocktails" in tools:
            return self._bartender(messages)
        return self._stylist(messages)

    # --- Fashion ----------------------------------------------------------------------------

    @staticmethod
    def _last_user(messages: list[dict]) -> str:
        return next((_text(m["content"]) for m in reversed(messages) if m["role"] == "user"), "")

    def _intent(self, messages: list[dict]) -> dict:
        text = self._last_user(messages).lower()
        if re.search(
            r"\b(save|shortlist|keep|remember|always|compare|goes with|outfit|thanks|picks)\b",
            text,
        ):
            return {"query": "", "wants_products": False}
        args: dict = {}
        if match := BUDGET.search(text):
            args["max_price"] = float(match.group(1))
        colours = [c.title() for c in COLOURS if re.search(rf"\b{c}\b", text)]
        if colours:
            args["colours"] = colours
        if re.search(r"\b(women|woman|ladies|her)\b", text):
            args["sections"] = ["Womens Everyday Collection"]
        query = BUDGET.sub("", text)
        query = re.sub(
            r"\b(for (women|men)|please|i need|show me|find me|some|a pair of)\b", "", query
        )
        args["query"] = re.sub(r"[^\w\s-]", " ", query).strip() or text
        return args

    def _stylist(self, messages: list[dict]) -> dict:
        last = messages[-1]
        system = _text(messages[0]["content"])
        user = self._last_user(messages)
        low = user.lower()
        if last["role"] == "tool":
            return {"content": self._list(PRODUCT_LINE.findall(_text(last["content"])), "tool")}
        if low.startswith("verification failed"):
            return {
                "content": self._list(self._products(messages), "Here are the verified options:")
            }
        if low.startswith("[decision on your proposal]"):
            approved = "approved" in low
            return {
                "content": "Done: it is saved, and you can review it under **Your session**."
                if approved
                else "No problem, nothing was changed. Want me to look for alternatives?"
            }
        products = self._products(messages)
        ids = [int(p[0]) for p in products]
        if "outfit" in low:
            return {
                "tool_calls": [
                    _tool_call(
                        "AskShopper",
                        {
                            "question": "Happy to help. What's the occasion?",
                            "options": ["Weekend casual", "Office", "Summer party"],
                        },
                    )
                ]
            }
        if re.search(r"\b(always|remember)\b", low):
            args: dict = {"sections": ["Menswear"] if re.search(r"\bmen", low) else []}
            if match := BUDGET.search(low):
                args["max_price"] = float(match.group(1))
            args = {k: v for k, v in args.items() if v}
            return {"tool_calls": [_tool_call("RememberPreferences", args or {"max_price": 50})]}
        if re.search(r"\b(save|shortlist|keep)\b", low) and ids:
            return {"tool_calls": [_tool_call("SaveToShortlist", {"product_ids": ids[:2]})]}
        if "goes with" in low:
            named = re.search(r"product (\d+)", low)
            target = int(named.group(1)) if named else (ids[0] if ids else None)
            if target:
                args = {"product_id": target, "relation": "complement"}
                return {"tool_calls": [_tool_call("FindRelated", args)]}
        if ("look like" in low or "similar" in low) and ids:
            args = {"product_id": ids[0], "relation": "similar"}
            return {"tool_calls": [_tool_call("FindRelated", args)]}
        if re.search(r"\b(history|picks)\b", low):
            return {"tool_calls": [_tool_call("PersonalPicks", {})]}
        if "compare" in low and len(ids) >= 2:
            return {"tool_calls": [_tool_call("InspectProducts", {"product_ids": ids[:2]})]}
        if "best deal" in low and products:
            _, name, _colour, price = products[0]
            invented = max(1.0, float(price) - 7.5)
            return {"content": f"The **{name.strip()}** is on offer at just €{invented:.2f} today."}
        current = PRODUCT_LINE.findall(system)
        return {"content": self._list(current, "Here are a few pieces that match:")}

    @staticmethod
    def _products(messages: list[dict]) -> list[tuple]:
        """Products the agent has seen: the system list first, then recent tool results."""
        seen: dict[str, tuple] = {}
        for message in [messages[0], *messages[-6:]]:
            for row in PRODUCT_LINE.findall(_text(message.get("content"))):
                seen.setdefault(row[0], row)
        return list(seen.values())

    @staticmethod
    def _list(rows: list[tuple], intro: str) -> str:
        if not rows:
            return "Nothing matched exactly. Try a broader description or a higher budget."
        if intro == "tool":
            intro = "Here's what I found:"
        lines = [intro]
        for _, name, colour, price in rows[:4]:
            lines.append(f"- **{name.strip()}** · {colour.strip()} · €{float(price):.2f}")
        return "\n".join(lines)

    # --- Cocktails ----------------------------------------------------------------------------

    def _bartender(self, messages: list[dict]) -> dict:
        last = messages[-1]
        user = self._last_user(messages)
        low = user.lower()
        if last["role"] == "tool":
            drinks = DRINK_LINE.findall(_text(last["content"]))
            if not drinks:
                return {"content": "Nothing matched. Try fewer or different ingredients."}
            lines = ["Here are drinks from the recipe catalog:"]
            lines += [f"- **{name.strip()}** ({label.strip()})" for _, name, label in drinks[:4]]
            return {"content": "\n".join(lines)}
        if low.startswith("verification failed"):
            return {"content": "Here are the drinks I could verify from the catalog."}
        if low.startswith("[decision on your proposal]"):
            if "approved" in low:
                return {"content": "Done, it's saved under **Your session**."}
            return {"content": "No problem, nothing was saved."}
        seen = [
            int(i)
            for i in re.findall(
                r"\[id (\d+)\]", "\n".join(_text(m.get("content")) for m in messages)
            )
        ]
        if re.search(r"\b(always|remember|don't drink|do not drink)\b", low):
            args: dict = {}
            if re.search(r"alcohol|don't drink|do not drink", low):
                args["alcohol_free"] = True
            if match := re.search(r"(?:avoid|no|allergic to)\s+([a-z ]+?)(?:[,.]|$)", low):
                args["avoided_ingredients"] = [match.group(1).strip()]
            return {"tool_calls": [_tool_call("RememberTastes", args or {"alcohol_free": True})]}
        if re.search(r"\b(save|keep)\b", low) and seen:
            return {"tool_calls": [_tool_call("SaveCocktails", {"drink_ids": seen[-1:]})]}
        if "similar to" in low and seen:
            return {"tool_calls": [_tool_call("SimilarCocktails", {"drink_id": seen[-1]})]}
        if match := re.search(r"(?:make with|i have|got)\s+(.+)", low):
            items = [
                w.strip(" ?.!") for w in re.split(r",| and ", match.group(1)) if w.strip(" ?.!")
            ]
            return {"tool_calls": [_tool_call("WhatCanIMake", {"ingredients": items[:8]})]}
        if re.search(r"alcohol-free|non-alcoholic|mocktail", low):
            args = {
                "query": re.sub(r"alcohol-free|non-alcoholic|mocktails?", "", low).strip(),
                "alcohol": "non_alcoholic",
            }
            return {"tool_calls": [_tool_call("SearchCocktails", args)]}
        return {"tool_calls": [_tool_call("SearchCocktails", {"query": user[:150]})]}


class _Handler(BaseHTTPRequestHandler):
    script = Script()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        decided = self.script.reply(body)
        message = {"role": "assistant", "content": decided.get("content")}
        if decided.get("tool_calls"):
            message["tool_calls"] = decided["tool_calls"]
        payload = {
            "id": f"chatcmpl-{next(_ids)}",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "stub"),
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if decided.get("tool_calls") else "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


def serve(port: int) -> ThreadingHTTPServer:
    """Start the stub on 127.0.0.1:`port` in a daemon thread; call .shutdown() to stop."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8790)
    port = parser.parse_args().port
    print(f"LLM stub on http://127.0.0.1:{port}/v1")
    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()
