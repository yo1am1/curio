"""A scripted, OpenAI-compatible chat completions server for end-to-end runs.

The app talks to it exactly as it talks to a real provider (LLM_PROVIDER=openai and
LLM_BASE_URL pointing here), so the whole production path runs: langchain-openai, HTTP,
tool-call parsing, the agent engine, guardrails, the API and the UI. Replies follow
deterministic rules keyed on the shopper's words, which makes every flow reproducible:

- the intent extractor (forced SearchIntent tool) gets keywords, colours, audience, budget;
- the fashion, cocktail and book agents get tool calls for searches, related pieces, pantry,
  readers-also-liked, clarifying questions and approval proposals, then answers built only
  from tool results;
- fashion "outfit" requests with an occasion call BuildOutfit (without one, AskShopper asks
  for the occasion first); "swap"/"cheaper" plus a slot of the current outfit call
  SwapOutfitItem, and "save ... outfit" proposes SaveToShortlist with every outfit piece;
- answers state catalog facts through fact references ("{product:ID} · {colour:ID} ·
  {price:ID}", "{book:ID} by {author:book:ID} · ★ {rating:book:ID}", "{drink:ID}
  ({alcohol:drink:ID})", "{total:price:ID,ID}"), which the server renders from the catalog,
  as the answer prompts ask; a few values are typed raw on purpose so the backstop check runs
  too: an outfit's budget and what is left, the pairing answers' "drink #N", and the
  inventions below;
- the phrases "best deal" (fashion) and "highest rated" (books) first get an answer with an
  invented price or rating, typed raw, so the backstop check and self-correction can be seen
  end to end;
- book requests to drink, pour or pair ("What should I drink with Dune?", "Pair a cocktail
  with my reading list") call PairCocktail with the title, the reading list or the book on
  screen; "another pairing" repeats the last subject without the drinks shown, and
  "alcohol-free" asks for alcohol-free drinks. Answers cite the drinks as "{drink:N} (drink
  #N, {alcohol:drink:N})" with the tool's first reason; "surprise me" invents a drink the
  pairing did not return, typed raw, twice, so the grounding fallback can be seen end to end;
- the evening host plans an evening with PlanEvening (the occasion, budget, audience and
  alcohol-free as written), asks for the occasion when the request names none ("plan my
  evening"), swaps a part of the current plan with SwapEveningPart ("another book", "another
  drink", "make the drink alcohol-free", "cheaper shoes") and proposes SaveEvening with every
  ID of the current plan ("save the evening"). Answers cite only the plan's lines; "surprise
  me" invents a drink twice, so the evening's grounding fallback can be seen end to end.

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
BOOK_LINE = re.compile(r"\[id (\d+)\] ([^|\n]+) \| ([^|\n]+) \| ★ ([\d.]+)")
BUDGET = re.compile(r"(?:under|below|less than|up to|budget(?: is| of)?)\s*€?\s*(\d+)")
COLOURS = ("black", "white", "blue", "red", "beige", "grey", "pink", "green", "brown")
OCCASION_WORDS = re.compile(
    r"\b(summer|winter|autumn|spring|wedding|office|work|party|beach|holiday|gym|casual|weekend"
    r"|interview|dinner|date|night out)\b"
)
OUTFIT_SLOTS = ("top", "bottom", "dress", "outerwear", "shoes", "bag", "accessory")
# "- Dress: [id 1] River Wrap Dress | Dress | Ladieswear | Light Red | €29.99 | ... | evidence: …"
OUTFIT_PIECE = re.compile(
    r"^- (\w+): \[id (\d+)\] [^|\n]+ \| [^|\n]+ \| [^|\n]+ \| [^|\n]+ \| €[\d.]+"
    r"(?:.*\| evidence: (.+))?$",
    re.MULTILINE,
)
OUTFIT_GAP = re.compile(r"^- (\w+): not filled\. (.+)$", re.MULTILINE)
# The budget and what is left are the tool's figures, typed raw (the backstop checks them).
OUTFIT_BUDGET = re.compile(r"total €[\d.]+((?: of a €[\d.]+ budget)?(?: \(€[\d.]+ left\))?)")
CURRENT_OUTFIT = re.compile(r"current outfit [^:]*: [^:]*: (.+?)\. (?:shortlist|the shortlist)")
# "1. drink #269 Old Fashioned | contains alcohol | Cocktail | ... | why: reason; reason"
PAIRED_LINE = re.compile(r"^\d+\. drink #(\d+) ([^|\n]+) \| ([^|\n]+) \|.*\| why: ([^;\n]+)", re.M)
DRINKS_SHOWN = re.compile(r"Drinks shown \(an earlier pairing\): (.+)$", re.M)
PAIR_WORDS = re.compile(r"\b(drink|drinks|cocktail|cocktails|pour|pair|pairing|sip)\b")
INVENTED_DRINK = "**Negroni** (drink #257)"
# The evening tool result (`render_plan`) and the session's current plan.
EVENING_THEME = re.compile(r"^Evening plan for “[^”]*” · theme (\w+)", re.M)
EVENING_CHANGE = re.compile(r"^Change: (.+)$", re.M)
EVENING_PIECE = re.compile(r"^- (\w+): product #(\d+) ", re.M)
EVENING_BUDGET = re.compile(r"^Outfit: total €[\d.]+((?: of a €[\d.]+ budget)?)", re.M)
EVENING_BOOK = re.compile(r"^Book: book #(\d+) ", re.M)
EVENING_DRINK = re.compile(r"^Drink: drink #(\d+) .*? \| why: ([^;\n]+)", re.M)
EVENING_MISSING = re.compile(r"^Missing (\w+): (.+)$", re.M)
EVENING_WHY = re.compile(r"^Why it works: (.+)$", re.M)
EVENING_SESSION = re.compile(r"current evening plan \([^)]*\): (.+?)\.(?: |$)", re.S)
_ids = count(1)


def _text(content) -> str:
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _total(ids: list[str]) -> str:
    """The total of the pieces' prices as a fact reference the server computes."""
    return f"{{total:price:{','.join(ids)}}}" if len(ids) > 1 else f"{{price:{ids[0]}}}"


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
        if "SearchBooks" in tools:
            return self._librarian(messages)
        if "PlanEvening" in tools:
            return self._host(messages)
        return self._stylist(messages)

    # --- Fashion ----------------------------------------------------------------------------

    @staticmethod
    def _last_user(messages: list[dict]) -> str:
        return next((_text(m["content"]) for m in reversed(messages) if m["role"] == "user"), "")

    def _intent(self, messages: list[dict]) -> dict:
        text = self._last_user(messages).lower()
        if self._last_assistant(messages).endswith("What's the occasion?"):
            return {"query": "", "wants_products": False}  # the answer is an outfit occasion
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
            result = _text(last["content"])
            if "Outfit for “" in result:
                return {"content": self._outfit_answer(result)}
            hinted = re.search(r"Try relation '(\w+)'", result)
            target = re.findall(r'"product_id": ?(\d+)', json.dumps(messages[-2].get("tool_calls")))
            if hinted and target:
                args = {"product_id": int(target[0]), "relation": hinted.group(1)}
                return {"tool_calls": [_tool_call("FindRelated", args)]}
            return {"content": self._list(PRODUCT_LINE.findall(result), "tool")}
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
        outfit = self._current_outfit(system)
        if outfit and re.search(r"\b(save|shortlist|keep)\b", low) and "outfit" in low:
            pieces = {"product_ids": [pid for _, pid in outfit][:5]}
            return {"tool_calls": [_tool_call("SaveToShortlist", pieces)]}
        slot = next((name for name, _ in outfit if re.search(rf"\b{name}\b", low)), None)
        if slot and re.search(r"\b(swap|another|cheaper|different)\b", low):
            args = {"slot": slot, "mode": "cheaper" if "cheaper" in low else "other"}
            return {"tool_calls": [_tool_call("SwapOutfitItem", args)]}
        asked = self._last_assistant(messages).endswith("What's the occasion?")
        if ("outfit" in low and OCCASION_WORDS.search(low)) or asked:
            return {"tool_calls": [_tool_call("BuildOutfit", self._outfit_request(low))]}
        if "outfit" in low:
            return {
                "tool_calls": [
                    _tool_call(
                        "AskShopper",
                        {
                            "question": "Happy to help. What's the occasion?",
                            "options": [
                                "Summer wedding under €80",
                                "Office for women",
                                "Weekend casual",
                            ],
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
    def _last_assistant(messages: list[dict]) -> str:
        for message in reversed(messages[1:-1]):
            if message["role"] == "assistant":
                return _text(message.get("content")).split("\nReferenced catalog items:")[0].strip()
        return ""

    @staticmethod
    def _current_outfit(system: str) -> list[tuple[str, int]]:
        """(slot, product id) of the outfit the session holds, from the system prompt."""
        match = CURRENT_OUTFIT.search(system)
        if not match:
            return []
        pieces = re.findall(r"(\w+) \[id (\d+)\]", match.group(1))
        return [(slot, int(pid)) for slot, pid in pieces if slot in OUTFIT_SLOTS]

    @staticmethod
    def _outfit_request(low: str) -> dict:
        words = [m.group(0) for m in OCCASION_WORDS.finditer(low)]
        args: dict = {"occasion": " ".join(dict.fromkeys(words)) or "casual"}
        if match := BUDGET.search(low):
            args["budget"] = float(match.group(1))
        if re.search(r"\b(for women|for her|ladies)\b", low):
            args["audience"] = "women"
        elif re.search(r"\b(for men|for him)\b", low):
            args["audience"] = "men"
        return args

    @staticmethod
    def _outfit_answer(result: str) -> str:
        """An answer built only from the BuildOutfit / SwapOutfitItem result."""
        intro = result.split("\n", 1)[0] if not result.startswith("Outfit for") else ""
        lines = [intro or "Here's the outfit I put together:"]
        pieces = OUTFIT_PIECE.findall(result)
        for slot, pid, reason in pieces:
            line = f"- **{slot}**: {{product:{pid}}} · {{colour:{pid}}} · {{price:{pid}}}"
            lines.append(line + (f" (_{reason.strip()}_)" if reason else ""))
        for slot, issue in OUTFIT_GAP.findall(result):
            lines.append(f"- **{slot}**: not filled. {issue.strip()}")
        if pieces and (budget := OUTFIT_BUDGET.search(result)):
            lines.append(f"\nTotal: {_total([pid for _, pid, _ in pieces])}{budget.group(1)}.")
        return "\n".join(lines)

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
        for pid, _name, _colour, _price in rows[:4]:
            lines.append(f"- {{product:{pid}}} · {{colour:{pid}}} · {{price:{pid}}}")
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
            lines += [f"- {{drink:{i}}} ({{alcohol:drink:{i}}})" for i, _, _ in drinks[:4]]
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

    # --- Books --------------------------------------------------------------------------------

    def _librarian(self, messages: list[dict]) -> dict:
        last = messages[-1]
        user = self._last_user(messages)
        low = user.lower()
        if last["role"] == "tool" and _text(last["content"]).startswith("Pairing:"):
            return {"content": self._pairing_answer(_text(last["content"]), low)}
        if last["role"] == "tool":
            books = BOOK_LINE.findall(_text(last["content"]))
            if not books:
                return {"content": _text(last["content"])[:200] or "Nothing matched."}
            lines = ["Here are books from the catalog:"]
            lines += [
                f"- {{book:{i}}} by {{author:book:{i}}} · ★ {{rating:book:{i}}}"
                for i, _, _, _ in books[:4]
            ]
            return {"content": "\n".join(lines)}
        if low.startswith("verification failed"):
            if "surprise me" in self._request(messages):
                return {"content": f"Trust me: pour a {INVENTED_DRINK}."}  # still invented
            return {"content": "Here are the books I could verify from the catalog."}
        if low.startswith("[decision on your proposal]"):
            if "approved" in low:
                return {"content": "Done, it's on your reading list under **Your session**."}
            return {"content": "No problem, nothing was saved."}
        # The books on screen: this turn's list, else the cards of the latest answer.
        current = BOOK_LINE.findall(_text(messages[0]["content"]))
        for message in reversed(messages[1:]):
            if current:
                break
            if message["role"] == "assistant":
                current = BOOK_LINE.findall(_text(message.get("content")))
        if PAIR_WORDS.search(low) or low in ("another pairing", "make it alcohol-free"):
            return {
                "tool_calls": [_tool_call("PairCocktail", self._pairing_args(messages, current))]
            }
        if "highest rated" in low and current:
            _, title, _author, _rating = current[0]
            return {"content": f"**{title.strip()}** has the best rating here: ★ 4.99."}
        if re.search(r"\b(remember|always|never|hate|can't stand)\b", low):
            args: dict = {}
            if match := re.search(
                r"(?:no|never(?: read)?|hate|don't like|avoid)\s+([a-z -]+?)(?:[,.]|$)", low
            ):
                args["avoided_genres"] = [match.group(1).strip().removesuffix(" books")]
            if match := re.search(r"(?:i love|i like|always)\s+([a-z -]+?)(?:[,.]|$)", low):
                args["liked_genres"] = [match.group(1).strip().removesuffix(" books")]
            return {
                "tool_calls": [
                    _tool_call("RememberReadingTastes", args or {"liked_genres": ["Fantasy"]})
                ]
            }
        if re.search(r"\b(save|keep|add)\b", low) and current:
            return {"tool_calls": [_tool_call("SaveBooks", {"book_ids": [int(current[0][0])]})]}
        if "also liked" in low and current:
            args = {"book_id": int(current[0][0])}
            return {"tool_calls": [_tool_call("ReadersAlsoLiked", args)]}
        if match := re.match(r"more by (.+?)[.…]*$", user, re.IGNORECASE):
            return {"tool_calls": [_tool_call("BooksByAuthor", {"author": match.group(1)})]}
        if re.fullmatch(r"(recommend|suggest) (me )?(a book|something)[.!?]*", low):
            question = {
                "question": "Happy to. What are you in the mood for?",
                "options": ["Fantasy", "A gripping thriller", "Something funny"],
            }
            return {"tool_calls": [_tool_call("AskShopper", question)]}
        if "shorter" in low:
            earlier = [
                _text(m["content"])
                for m in messages[1:-1]
                if m["role"] == "user" and "shorter" not in _text(m["content"]).lower()
            ]
            query = earlier[-1][:150] if earlier else "novel"
            return {"tool_calls": [_tool_call("SearchBooks", {"query": query, "max_pages": 250})]}
        return {"tool_calls": [_tool_call("SearchBooks", {"query": user[:150]})]}

    @staticmethod
    def _request(messages: list[dict]) -> str:
        """The shopper's request of this turn (the self-correction message is the stub's)."""
        for message in reversed(messages):
            text = _text(message.get("content")).lower()
            if message["role"] == "user" and not text.startswith("verification failed"):
                return text
        return ""

    def _pairing_args(self, messages: list[dict], current: list[tuple]) -> dict:
        user = self._last_user(messages)
        low = user.lower()
        args: dict = {}
        if low in ("another pairing", "make it alcohol-free"):
            # Repeat the last pairing request, without the drinks already shown.
            earlier = [
                _text(m["content"])
                for m in messages[1:-1]
                if m["role"] == "user" and PAIR_WORDS.search(_text(m["content"]).lower())
            ]
            if earlier:
                args = self._pairing_args(
                    [messages[0], {"role": "user", "content": earlier[-1]}], current
                )
            if low == "another pairing":
                for message in reversed(messages[1:-1]):
                    if message["role"] == "assistant":
                        shown = DRINKS_SHOWN.search(_text(message.get("content")))
                        if shown:
                            ids = [int(i) for i in re.findall(r"drink #(\d+)", shown.group(1))]
                            args["exclude_drink_ids"] = ids[:15]
                        break
            else:
                args["alcohol_free"] = True
            return args or {"use_reading_list": True}
        if "alcohol-free" in low or "alcohol free" in low or "no alcohol" in low:
            args["alcohol_free"] = True
        if "reading list" in low:
            return args | {"use_reading_list": True}
        if match := re.search(r".*\b(?:with|for)\s+(.+?)[?.!]*$", user):
            title = re.sub(r"^(?:reading |my reading of )", "", match.group(1)).strip()
            if title.lower() not in ("me", "it", "this", "this book"):
                return args | {"title": title[:200]}
        if current:
            return args | {"book_id": int(current[0][0])}
        return args | {"use_reading_list": True}

    @staticmethod
    def _pairing_answer(result: str, low: str) -> str:
        """An answer built only from the PairCocktail result (or an invented drink)."""
        drinks = PAIRED_LINE.findall(result)
        if not drinks:
            return "No recipe in the catalog fits that pairing. Try another book."
        if "surprise me" in low:
            return f"Forget the list: pour a {INVENTED_DRINK} tonight."
        summary = result.split("\n", 1)[0].removeprefix("Pairing: ").rstrip(".")
        lines = [f"{summary}. Here's what to pour:"]
        for drink_id, _name, _label, reason in drinks[:3]:
            lines.append(
                f"- {{drink:{drink_id}}} (drink #{drink_id}, {{alcohol:drink:{drink_id}}}): "
                f"{reason.strip()}"
            )
        if "contains alcohol" in result:
            lines.append("\nPlease drink responsibly.")
        return "\n".join(lines)

    # --- Evening ------------------------------------------------------------------------------

    def _host(self, messages: list[dict]) -> dict:
        last = messages[-1]
        user = self._last_user(messages)
        low = user.lower()
        request = self._request(messages)
        if last["role"] == "tool":
            result = _text(last["content"])
            if not result.startswith("Evening plan for"):
                return {"content": result[:300]}
            if "surprise me" in request:
                return {"content": f"Skip the plan and pour a {INVENTED_DRINK}."}
            return {"content": self._evening_answer(result)}
        if low.startswith("verification failed"):
            if "surprise me" in request:
                return {"content": f"Trust me: a {INVENTED_DRINK} is the one."}  # still invented
            return {"content": "Here is the evening I could verify from the catalogs."}
        if low.startswith("[decision on your proposal]"):
            if "approved" in low:
                return {"content": "Done: each part is saved to its own list."}
            return {"content": "No problem, nothing was saved."}
        system = _text(messages[0]["content"])
        plan = EVENING_SESSION.search(system)
        current = plan.group(1) if plan else ""
        if current and re.search(r"\b(save|keep)\b", low):
            args = {
                "product_ids": [int(i) for i in re.findall(r"product #(\d+)", current)][:5],
                "book_ids": [int(i) for i in re.findall(r"book #(\d+)", current)][:2],
                "drink_ids": [int(i) for i in re.findall(r"drink #(\d+)", current)][:2],
            }
            return {"tool_calls": [_tool_call("SaveEvening", {k: v for k, v in args.items() if v})]}
        if current and re.search(r"\b(another|different|other) book\b", low):
            return {"tool_calls": [_tool_call("SwapEveningPart", {"part": "book"})]}
        if current and "drink" in low and re.search(r"alcohol[- ]free|no alcohol", low):
            args = {"part": "drink", "mode": "alcohol_free"}
            return {"tool_calls": [_tool_call("SwapEveningPart", args)]}
        if current and re.search(r"\b(another|different|other) drink\b", low):
            return {"tool_calls": [_tool_call("SwapEveningPart", {"part": "drink"})]}
        slots = re.findall(r"(\w+) product #\d+", current)
        slot = next((name for name in slots if re.search(rf"\b{name}\b", low)), None)
        if slot and re.search(r"\b(swap|another|cheaper|different)\b", low):
            args = {
                "part": "outfit",
                "slot": slot,
                "mode": "cheaper" if "cheaper" in low else "other",
            }
            return {"tool_calls": [_tool_call("SwapEveningPart", args)]}
        occasion = re.sub(
            r"^(?:please\s+)?(?:plan|organise|organize)\s+(?:me\s+)?(?:an?\s+|my\s+)?", "", low
        ).strip(" .!?")
        if not occasion or occasion in ("evening", "my evening", "something", "an evening"):
            question = {
                "question": "Happy to. What's the occasion?",
                "options": ["Rainy Sunday in", "Date night under €100", "Book club, alcohol-free"],
            }
            return {"tool_calls": [_tool_call("AskShopper", question)]}
        args: dict = {"occasion": user.strip(" .!?")[:200]}
        if match := BUDGET.search(low):
            args["budget"] = float(match.group(1))
        if re.search(r"alcohol[- ]free|no alcohol|sober", low):
            args["alcohol_free"] = True
        if re.search(r"\b(for men|for him)\b", low):
            args["audience"] = "men"
        elif re.search(r"\b(for women|for her)\b", low):
            args["audience"] = "women"
        return {"tool_calls": [_tool_call("PlanEvening", args)]}

    @staticmethod
    def _evening_answer(result: str) -> str:
        """An answer built only from the PlanEvening / SwapEveningPart result."""
        theme = EVENING_THEME.search(result)
        lines = [f"Here's your {theme.group(1).lower()} evening." if theme else "Your evening:"]
        if change := EVENING_CHANGE.search(result):
            lines.insert(0, change.group(1))
        pieces = EVENING_PIECE.findall(result)
        if pieces:
            lines.append("\n**Outfit**")
            for slot, pid in pieces:
                lines.append(f"- {slot}: {{product:{pid}}} (product #{pid}) · {{price:{pid}}}")
            if budget := EVENING_BUDGET.search(result):
                lines.append(f"- Total: {_total([pid for _, pid in pieces])}{budget.group(1)}")
        if book := EVENING_BOOK.search(result):
            ref = f"book:{book.group(1)}"
            lines.append(
                f"\n**Book**: {{{ref}}} (book #{book.group(1)}) by {{author:{ref}}} · "
                f"★ {{rating:{ref}}}"
            )
        if drink := EVENING_DRINK.search(result):
            drink_id, reason = drink.groups()
            lines.append(
                f"\n**Drink**: {{drink:{drink_id}}} (drink #{drink_id}, "
                f"{{alcohol:drink:{drink_id}}}): {reason.strip()}"
            )
        for part, reason in EVENING_MISSING.findall(result):
            lines.append(f"\nNo {part} yet: {reason.strip()}")
        if why := EVENING_WHY.search(result):
            lines.append(f"\n_Why it works_: {why.group(1).strip()}")
        if "contains alcohol" in result:
            lines.append("\nPlease drink responsibly.")
        return "\n".join(lines)


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
