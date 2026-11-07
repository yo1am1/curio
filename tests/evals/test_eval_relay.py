"""The relay and its answering CLI: a round trip through files with a scripted responder."""

import json
import socket
import threading
import time

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from curio.domains.cocktails.agent.tools import COCKTAIL_TOOLS
from curio.domains.fashion.agent.intent import SearchIntent
from evals import relay_answer
from evals.relay import (
    RelayQueue,
    completion,
    is_loopback,
    serve,
    stream_chunks,
    write_json_atomic,
)
from evals.relay import main as relay_main
from evals.relay_answer import AnswerError, answer, build_answer, render


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Responder:
    """Answers pending requests in a background thread with `decide(request)`."""

    def __init__(self, queue: RelayQueue, decide) -> None:
        self.queue = queue
        self.decide = decide
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            for seq in self.queue.pending():
                text, calls = self.decide(self.queue.read_request(seq))
                try:
                    answer(self.queue, seq, text, calls)
                except AnswerError as error:
                    self.errors.append(str(error))
            time.sleep(0.02)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)


@pytest.fixture
def relay(tmp_path):
    port = _free_port()
    server = serve(port, tmp_path / "queue", timeout=1.5, poll=0.02)
    yield f"http://127.0.0.1:{port}/v1", RelayQueue(tmp_path / "queue")
    server.shutdown()


@pytest.fixture
def model(relay):
    url, _ = relay
    return ChatOpenAI(
        model="relay-test",
        base_url=url,
        api_key="not-a-secret",
        temperature=0.2,
        timeout=10,
        max_retries=0,
        http_client=httpx.Client(trust_env=False),
    )


def test_tool_calls_round_trip_through_files(relay, model):
    _, queue = relay

    def decide(request):
        return None, [{"name": "SearchCocktails", "arguments": {"query": "sour", "alcohol": "any"}}]

    responder = Responder(queue, decide)
    try:
        reply = model.bind_tools(list(COCKTAIL_TOOLS.values())).invoke("a sour drink")
    finally:
        responder.stop()
    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["name"] == "SearchCocktails"
    assert reply.tool_calls[0]["args"] == {"query": "sour", "alcohol": "any"}
    assert reply.tool_calls[0]["id"] == "call_000001_1"
    stored = queue.read_request("000001")
    assert stored["model"] == "relay-test"
    assert stored["temperature"] == 0.2
    assert stored["messages"] == [{"role": "user", "content": "a sour drink"}]
    assert {t["function"]["name"] for t in stored["tools"]} == set(COCKTAIL_TOOLS)
    assert queue.state("000001") == "answered"
    assert not responder.errors


def test_structured_output_is_a_forced_function_call(relay, model):
    """The fashion intent extractor uses function calling, so the answer is one forced call."""
    _, queue = relay
    seen = []

    def decide(request):
        seen.append(request)
        return None, [{"name": "SearchIntent", "arguments": {"query": "black trousers"}}]

    responder = Responder(queue, decide)
    try:
        structured = model.with_structured_output(
            SearchIntent, method="function_calling", include_raw=True
        )
        result = structured.invoke("black trousers under 30")
    finally:
        responder.stop()
    assert result["parsed"].query == "black trousers"
    assert seen[0]["tool_choice"] == {"type": "function", "function": {"name": "SearchIntent"}}
    assert relay_answer.forced_tool(seen[0]) == "SearchIntent"
    with pytest.raises(AnswerError, match="forces one call to SearchIntent"):
        build_answer(seen[0], text="black trousers")


def test_text_answers_and_unanswered_requests(relay, model):
    url, queue = relay
    responder = Responder(queue, lambda request: ("Two drinks fit.", []))
    try:
        assert model.invoke("hello").content == "Two drinks fit."
    finally:
        responder.stop()
    started = time.monotonic()
    response = httpx.post(
        f"{url}/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "x"}]},
        timeout=20,
        trust_env=False,
    )
    assert response.status_code == 504
    assert time.monotonic() - started >= 1.4
    assert queue.state("000002") == "expired"
    assert queue.pending() == []
    with pytest.raises(AnswerError, match="expired"):
        answer(queue, "2", "late")


def test_models_and_health_endpoints(relay):
    url, _ = relay
    assert httpx.get(f"{url}/models", trust_env=False).json()["data"][0]["id"] == "relay"
    assert httpx.get(url, trust_env=False).json()["status"] == "ok"


def test_a_token_is_required_on_every_request_when_set(tmp_path):
    port = _free_port()
    server = serve(port, tmp_path / "queue", timeout=1.5, poll=0.02, token="s3cret")
    url = f"http://127.0.0.1:{port}/v1"
    try:
        assert httpx.get(f"{url}/models", trust_env=False).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert httpx.get(url, headers=wrong, trust_env=False).status_code == 401
        posted = httpx.post(f"{url}/chat/completions", json={"model": "m"}, trust_env=False)
        assert posted.status_code == 401
        assert not RelayQueue(tmp_path / "queue").sequences()  # nothing was queued
        right = {"Authorization": "Bearer s3cret"}
        assert httpx.get(url, headers=right, trust_env=False).json()["status"] == "ok"
    finally:
        server.shutdown()


def test_a_token_reaches_the_relay_through_the_openai_client(tmp_path):
    port = _free_port()
    server = serve(port, tmp_path / "queue", timeout=1.5, poll=0.02, token="s3cret")
    queue = RelayQueue(tmp_path / "queue")
    responder = Responder(queue, lambda request: ("Hello.", None))
    try:
        model = ChatOpenAI(
            model="relay", base_url=f"http://127.0.0.1:{port}/v1", api_key="s3cret", max_retries=0
        )
        assert model.invoke("hi").content == "Hello."
    finally:
        responder.stop()
        server.shutdown()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_a_non_loopback_relay_needs_a_token(tmp_path, host):
    with pytest.raises(ValueError, match="without a token"):
        serve(_free_port(), tmp_path / "queue", host=host)


def test_the_cli_refuses_a_public_relay_without_a_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RELAY_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        relay_main(["--host", "0.0.0.0", "--port", str(_free_port()), "--queue", str(tmp_path)])
    assert "without a token" in capsys.readouterr().err


def test_loopback_hosts():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("example.org")


def test_sequence_numbers_continue_after_a_restart(tmp_path):
    queue = RelayQueue(tmp_path)
    write_json_atomic(queue.request_path("000007"), {"messages": []})
    port = _free_port()
    server = serve(port, tmp_path, timeout=0.2, poll=0.02)
    try:
        httpx.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json={"messages": []},
            timeout=5,
            trust_env=False,
        )
    finally:
        server.shutdown()
    assert queue.sequences() == ["000007", "000008"]
    assert not list(tmp_path.glob(".*.tmp")), "atomic writes leave no temporary files"


def test_completion_and_stream_chunks():
    answer_ = {"content": None, "tool_calls": [{"name": "A", "arguments": {"x": 1}}]}
    payload = completion("000003", "m", answer_)
    message = payload["choices"][0]["message"]
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["finish_reason"] == "tool_calls"
    assert message["tool_calls"][0]["function"] == {"name": "A", "arguments": '{"x": 1}'}
    assert payload["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    first, last = stream_chunks(payload)
    assert first["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert last["choices"][0]["finish_reason"] == "tool_calls"
    text = completion("000004", "m", {"content": "hi"})
    assert text["choices"][0]["finish_reason"] == "stop"
    assert "tool_calls" not in text["choices"][0]["message"]


# --- The answering CLI ----------------------------------------------------------------------

REQUEST = {
    "model": "m",
    "temperature": 0.2,
    "messages": [
        {"role": "system", "content": "You are Curio."},
        {"role": "user", "content": "A sour drink?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "SearchCocktails", "arguments": '{"query": "sour"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "1. [id 405] Whiskey Sour"},
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": schema.__doc__,
                "parameters": schema.model_json_schema(),
            },
        }
        for name, schema in COCKTAIL_TOOLS.items()
    ],
}


@pytest.fixture
def queue(tmp_path):
    queue = RelayQueue(tmp_path)
    write_json_atomic(queue.request_path("000001"), REQUEST)
    return queue


def run_cli(queue, *args, capsys):
    code = relay_answer.main(["--queue", str(queue.root), *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_lists_and_shows_a_request(queue, capsys):
    code, out, _ = run_cli(queue, "list", capsys=capsys)
    assert code == 0
    assert "000001  [after a tool result]  7 tools  user: A sour drink?" in out
    code, out, _ = run_cli(queue, "show", capsys=capsys)
    assert code == 0
    assert "--- [system]\nYou are Curio." in out
    assert '(tool call call_1) SearchCocktails {"query": "sour"}' in out
    assert "--- [tool result for call_1]\n1. [id 405] Whiskey Sour" in out
    assert "* SaveCocktails: Propose saving catalog drinks" in out
    assert '"drink_ids": {"items": {"exclusiveMinimum": 0, "type": "integer"}' in out
    assert '"title"' not in out
    assert "answer 000001 --text" in out


def test_cli_writes_text_and_tool_call_answers(queue, capsys):
    code, out, _ = run_cli(queue, "answer", "1", "--text", "The **Whiskey Sour**.", capsys=capsys)
    assert (code, out.strip()) == (0, "Answered 000001: text.")
    assert queue.read_response("000001") == {"content": "The **Whiskey Sour**."}
    code, _, err = run_cli(queue, "answer", "1", "--text", "again", capsys=capsys)
    assert code == 2
    assert "already answered" in err
    args = ["answer", "1", "--force", "--tool", "SaveCocktails", '{"drink_ids": [405]}']
    code, out, _ = run_cli(queue, *args, capsys=capsys)
    assert (code, out.strip()) == (0, "Answered 000001: SaveCocktails.")
    response = queue.read_response("000001")
    assert response["tool_calls"] == [{"name": "SaveCocktails", "arguments": {"drink_ids": [405]}}]
    code, out, _ = run_cli(queue, "list", capsys=capsys)
    assert out.strip() == "No pending requests."


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["--tool", "Shout", "{}"], "Unknown tool 'Shout'"),
        (["--tool", "SaveCocktails", "{drink_ids: [1]}"], "not valid JSON"),
        (["--tool", "SaveCocktails", "[405]"], "must be a JSON object"),
        (["--tool", "SaveCocktails", '{"drink_ids": []}'], "needs at least 1 items"),
        (["--tool", "SaveCocktails", '{"drink_ids": [0]}'], "must be greater than 0"),
        (["--tool", "SaveCocktails", '{"drink_ids": [1], "x": 1}'], "unknown field 'x'"),
        (["--tool", "InspectCocktails", "{}"], "missing required field 'drink_ids'"),
        (["--tool", "SearchCocktails", '{"alcohol": "lots"}'], "is not one of"),
        (["--tool-calls", "{oops"], "--tool-calls is not valid JSON"),
        ([], "Give a text answer or at least one tool call"),
    ],
)
def test_cli_rejects_invalid_answers(queue, capsys, args, error):
    code, _, err = run_cli(queue, "answer", "1", *args, capsys=capsys)
    assert code == 2
    assert error in err
    assert queue.state("000001") == "pending"


def test_cli_answers_from_a_json_file_with_several_calls(queue, tmp_path, capsys):
    path = tmp_path / "answer.json"
    calls = [
        {"name": "InspectCocktails", "arguments": {"drink_ids": [405]}},
        {"name": "SimilarCocktails", "arguments": {"drink_id": 405}},
    ]
    path.write_text(json.dumps({"tool_calls": calls}))
    code, out, _ = run_cli(queue, "answer", "1", "--json-file", str(path), capsys=capsys)
    assert (code, out.strip()) == (0, "Answered 000001: InspectCocktails, SimilarCocktails.")


def test_cli_unknown_request(queue, capsys):
    code, _, err = run_cli(queue, "answer", "9", "--text", "hi", capsys=capsys)
    assert code == 2
    assert "No relay request 000009" in err


def test_response_format_json_schema_is_checked():
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_price": {"type": "number"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    request = {
        "messages": [],
        "response_format": {"type": "json_schema", "json_schema": {"name": "S", "schema": schema}},
    }
    assert build_answer(request, '{"query": "jeans", "max_price": 30}')["content"]
    with pytest.raises(AnswerError, match="wants JSON text"):
        build_answer(request, "jeans")
    with pytest.raises(AnswerError, match="missing required field 'query'"):
        build_answer(request, '{"max_price": 30}')
    assert "Response schema:" in render("000001", request)


def test_parallel_tool_calls_false_allows_one_call():
    request = {
        "messages": [],
        "parallel_tool_calls": False,
        "tools": REQUEST["tools"],
    }
    calls = [
        {"name": "InspectCocktails", "arguments": {"drink_ids": [1]}},
        {"name": "InspectCocktails", "arguments": {"drink_ids": [2]}},
    ]
    with pytest.raises(AnswerError, match="only one tool call"):
        build_answer(request, None, calls)


def test_cli_wait_returns_the_oldest_pending(queue, capsys):
    code, out, _ = run_cli(queue, "wait", "--timeout", "1", capsys=capsys)
    assert (code, out.strip()) == (0, "000001")
    answer(queue, "1", "done")
    code, out, _ = run_cli(queue, "wait", "--timeout", "0.6", capsys=capsys)
    assert code == 3


def test_cli_reads_answers_from_standard_input(queue, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("It's the **Whiskey Sour** (contains alcohol).\n"))
    code, _, _ = run_cli(queue, "answer", "1", "--text-file", "-", capsys=capsys)
    assert code == 0
    assert (
        queue.read_response("000001")["content"] == "It's the **Whiskey Sour** (contains alcohol)."
    )
