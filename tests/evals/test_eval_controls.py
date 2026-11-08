"""The control policies: each answers every kind of request the app makes, through the same
checks as the answering CLI, and none of them is useful."""

import json

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from curio.domains.books.agent.tools import BOOK_TOOLS
from curio.domains.cocktails.agent.tools import COCKTAIL_TOOLS
from curio.domains.evening.agent.tools import EVENING_TOOLS
from curio.domains.fashion.agent.intent import SearchIntent
from curio.domains.fashion.agent.tools import TOOLS as FASHION_TOOLS
from curio.engine.tools import AskShopper
from evals.controls import ASK_QUESTION, POLICIES, REFUSAL, list_names
from evals.relay import RelayQueue, write_json_atomic
from evals.scripted_responder import answer_pending

DOMAINS = {
    "fashion": FASHION_TOOLS,
    "cocktails": COCKTAIL_TOOLS,
    "books": BOOK_TOOLS,
    "evening": EVENING_TOOLS,
}
SEARCH = {
    "fashion": "SearchCatalog",
    "cocktails": "SearchCocktails",
    "books": "SearchBooks",
    "evening": "PlanEvening",
}
MESSAGE = "A cosy evening in with a good mystery, please. " * 8  # longer than some limits


def request(domain, messages, forced=None):
    tools = [convert_to_openai_tool(t) for t in DOMAINS[domain].values()]
    if "AskShopper" not in DOMAINS[domain]:
        tools.append(convert_to_openai_tool(AskShopper))
    body = {"model": "relay", "messages": messages, "tools": tools}
    if forced:
        body["tools"] = [convert_to_openai_tool(forced)]
        body["tool_choice"] = {"type": "function", "function": {"name": forced.__name__}}
    return body


def answered(tmp_path, policy, body, capsys):
    """Queue one request and let the policy answer it through the CLI's checks."""
    queue = RelayQueue(tmp_path / policy)
    queue.root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(queue.request_path("000001"), body)
    assert answer_pending(queue, POLICIES[policy]) == 1
    assert "rejected" not in capsys.readouterr().out  # the answer fit the request
    return queue.read_response("000001")


def tool_calls(message):
    return [c["name"] for c in message.get("tool_calls") or []]


def arguments(message):
    return json.dumps(message["tool_calls"][0]["arguments"])


USER = [{"role": "system", "content": "You are Curio."}, {"role": "user", "content": MESSAGE}]


@pytest.mark.parametrize("domain", DOMAINS)
def test_always_ask_asks_without_searching(tmp_path, domain, capsys):
    message = answered(tmp_path, "always-ask", request(domain, USER), capsys)
    assert tool_calls(message) == ["AskShopper"]
    assert ASK_QUESTION in arguments(message)


@pytest.mark.parametrize("domain", DOMAINS)
def test_always_refuse_answers_text_without_tools(tmp_path, domain, capsys):
    message = answered(tmp_path, "always-refuse", request(domain, USER), capsys)
    assert tool_calls(message) == []
    assert message["content"] == REFUSAL


@pytest.mark.parametrize("domain", DOMAINS)
def test_first_tool_echo_searches_once_with_the_raw_message(tmp_path, domain, capsys):
    message = answered(tmp_path, "first-tool-echo", request(domain, USER), capsys)
    assert tool_calls(message) == [SEARCH[domain]]
    assert "cosy evening in" in arguments(message)


def test_first_tool_echo_lists_names_without_facts(tmp_path, capsys):
    result = (
        "1. [id 141] Gin Lemon | contains alcohol | Cocktail | ingredients: Gin (6 cl)\n"
        "2. [id 129] Gimlet | contains alcohol | Cocktail | ingredients: Gin (2 1/2 oz)"
    )
    messages = [
        *USER,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "SearchCocktails", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": result},
    ]
    message = answered(tmp_path, "first-tool-echo", request("cocktails", messages), capsys)
    assert tool_calls(message) == []
    text = message["content"]
    assert text == "Here is what the catalog search returned: **Gin Lemon**, **Gimlet**."
    assert "alcohol" not in text and "cl" not in text


def test_names_from_evening_and_pairing_results():
    content = (
        "- Dress: product #608007005 Meet the Parents dress | Dress | Light Purple | €34.99\n"
        "Book: book #562 The Way of Kings | Brandon Sanderson | ★ 4.64\n"
        "Drink: drink #154 Honey Bee | contains alcohol | ingredients: White Rum (6 cl)"
    )
    body = {"messages": [{"role": "tool", "content": content}]}
    assert list_names(body) == (
        "Here is what the catalog search returned: **Meet the Parents dress**, "
        "**The Way of Kings**, **Honey Bee**."
    )
    assert "nothing" in list_names({"messages": [{"role": "tool", "content": "(none)"}]})


@pytest.mark.parametrize("policy", POLICIES)
def test_every_policy_obeys_a_forced_structured_output_call(tmp_path, policy, capsys):
    body = request("fashion", USER, forced=SearchIntent)
    message = answered(tmp_path, policy, body, capsys)
    assert tool_calls(message) == ["SearchIntent"]
    wants = message["tool_calls"][0]["arguments"]["wants_products"]
    # Only the echo control lets the shopper's raw message search the catalog.
    assert wants is (policy == "first-tool-echo")
