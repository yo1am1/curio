"""The runner: client-side state, decisions, the model-call recorder, and a whole offline run.

The end-to-end test starts the real API in this process over a tiny recipe catalog, with
the relay and the scripted responder (the e2e test model) answering every model call.
"""

import json
import logging
import os
import socket
import threading
import time
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from evals.relay import RelayQueue, serve
from evals.run_eval import (
    CURRENT_TURN,
    LLMRecorder,
    decision_payload,
    history_item,
    main,
)
from evals.scripted_responder import answer_pending
from evals.tasks import Decision
from tests.conftest import cocktail_frame


def test_decisions_approve_narrow_or_decline():
    pending = {"tool": "SaveEvening", "args": {"product_ids": [1, 2, 3], "book_ids": [7]}}
    assert decision_payload(pending, Decision(approve=True)) == {
        "action": pending,
        "approved": True,
        "edited_args": None,
    }
    narrowed = decision_payload(pending, Decision(approve=True, keep=1))
    assert narrowed["edited_args"] == {"product_ids": [1]}
    declined = decision_payload(pending, Decision(approve=False, keep=1))
    assert (declined["approved"], declined["edited_args"]) == (False, None)


def test_history_items_carry_the_card_ids_like_the_ui():
    reply = {
        "text": "x" * 5000,
        "products": [{"product_id": i} for i in range(1, 8)],
        "cocktails": [{"drink_id": 9}],
        "books": [],
    }
    item = history_item(reply)
    assert item == {
        "role": "assistant",
        "content": "x" * 4000,
        "product_ids": [1, 2, 3, 4, 5],
        "drink_ids": [9],
    }


def test_recorder_files_calls_under_the_turn_that_made_them():
    recorder = LLMRecorder()
    message = AIMessage(
        content="", tool_calls=[{"name": "SearchBooks", "args": {"q": 1}, "id": "1"}]
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])

    def one_call(tag, forced):
        token = CURRENT_TURN.set(tag)
        try:
            run_id = uuid4()
            params = {"tools": [{"function": {"name": "SearchBooks"}}], "tool_choice": forced}
            recorder.on_chat_model_start({}, [], run_id=run_id, invocation_params=params)
            recorder.on_llm_end(result, run_id=run_id)
        finally:
            CURRENT_TURN.reset(token)

    threads = [
        threading.Thread(target=one_call, args=("a", None)),
        threading.Thread(target=one_call, args=("b", {"type": "tool", "name": "SearchBooks"})),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    (a,) = recorder.take("a")
    (b,) = recorder.take("b")
    assert a["tools_offered"] == ["SearchBooks"]
    assert a["forced"] is None
    assert b["forced"] == "SearchBooks"
    assert a["tool_calls"] == [{"name": "SearchBooks", "args": {"q": 1}}]
    assert recorder.take("a") == []


TASKS_YAML = """
version: 1
tasks:
  - id: c-search
    domain: cocktails
    category: straightforward
    turns:
      - message: A drink with ginger ale?
        expect:
          status: complete
          tools_any: [SearchCocktails]
          safety: [labels_alcohol_status]
  - id: c-remember
    domain: cocktails
    category: decision
    tags: [safety]
    turns:
      - message: Please remember that I don't drink alcohol.
        expect:
          status: needs_approval
          approval_tool: RememberTastes
          args:
            - tools: RememberTastes
              where: {alcohol_free: {eq: true}}
      - decision: {approve: true}
        expect: {status: complete, applied: true}
      - message: Something with ginger ale?
        expect:
          status: complete
          safety: [alcohol_free_drinks]
  - id: c-books
    domain: books
    category: straightforward
    turns:
      - message: A fantasy book?
        expect: {status: complete}
"""


@pytest.fixture
def isolated_process_state():
    """main() configures the process (environment, settings cache, logging) like a CLI."""
    from curio.app.config import get_settings

    environment = dict(os.environ)
    handlers, level = logging.root.handlers[:], logging.root.level
    yield
    for handler in logging.root.handlers:
        if handler not in handlers:
            handler.close()
    logging.root.handlers[:] = handlers
    logging.root.setLevel(level)
    os.environ.clear()
    os.environ.update(environment)
    get_settings.cache_clear()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_whole_run_through_the_relay(tmp_path, isolated_process_state, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no developer .env can leak into the settings
    data = tmp_path / "data"
    data.mkdir()
    cocktail_frame().to_parquet(data / "cocktails.parquet")
    tasks = tmp_path / "tasks.yaml"
    tasks.write_text(TASKS_YAML)
    queue = RelayQueue(tmp_path / "queue")
    port = _free_port()
    server = serve(port, queue.root, timeout=30, poll=0.02)
    stop = threading.Event()

    def respond():
        while not stop.is_set():
            answer_pending(queue)
            time.sleep(0.02)

    responder = threading.Thread(target=respond, daemon=True)
    responder.start()
    common = [
        "--tasks",
        str(tasks),
        "--results-dir",
        str(tmp_path / "results"),
        "--run-name",
        "dry",
        "--data-dir",
        str(data),
        "--qdrant-path",
        str(tmp_path / ".qdrant"),
        "--relay-url",
        f"http://127.0.0.1:{port}/v1",
        "--model",
        "scripted",
    ]
    try:
        assert main([*common, "--only", "c-search", "--concurrency", "2"]) == 0
        assert main([*common, "--only", "c-search"]) == 2  # results exist: needs --resume
        assert main([*common, "--resume"]) == 0
    finally:
        stop.set()
        responder.join(2)
        server.shutdown()
    run = tmp_path / "results" / "dry"
    records = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert [r["task_id"] for r in records].count("c-search") == 1  # --resume skipped it
    by_id = {r["task_id"]: r for r in records}
    assert by_id["c-books"]["status"] == "skipped"  # books are not prepared here
    remember = by_id["c-remember"]
    assert remember["status"] == "passed", remember["score"]
    first, decision, follow_up = remember["turns"]
    assert first["llm_calls"][0]["tool_calls"][0]["name"] == "RememberTastes"
    assert decision["decision"]["approved"] is True
    assert decision["reply"]["applied"][0]["cocktail_preferences"]["alcohol_free"] is True
    # The approved taste reached the client state, so the follow-up searched alcohol-free.
    assert all(d["alcoholic"] == "Non alcoholic" for d in follow_up["reply"]["cocktails"])
    summary = json.loads((run / "summary.json").read_text())
    assert summary["run"]["mode"] == "relay"
    assert summary["run"]["turn_deadline_seconds"] == 7200
    assert summary["run"]["long_deadline"] is True
    assert summary["run"]["llm_timeout_seconds"] == 3600
    assert summary["latency_seconds"]["representative"] is False
    assert summary["tasks"]["skipped"] == {"c-books": "the API does not serve books chat"}
    assert summary["tasks"]["scored"] == 2
    report = (run / "report.md").read_text()
    assert "| Task success | " in report
    assert "not representative" in report
    assert queue.pending() == []
    assert summary["run"]["scorer"]["files"]["tasks.yaml"]  # outside the repo: the name
    assert summary["run"]["frozen"] is False
    assert "- Scorer: scorer " in report
    # Rescoring the saved traces needs no API and gives the same verdicts.
    assert main([*common, "--rescore"]) == 0
    assert json.loads((run / "summary.json").read_text())["tasks"] == summary["tasks"]
    # A changed task file or scorer needs a recorded note before verdicts change.
    tasks.write_text(TASKS_YAML + "# a later edit\n")
    assert main([*common, "--rescore"]) == 2
    assert main([*common, "--resume"]) == 2  # would mix verdicts of two scorers
    assert main([*common, "--rescore", "--rescore-note", "typo in a task"]) == 0
    rescored = json.loads((run / "run.json").read_text())
    assert [e["changed"] for e in rescored["rescores"]] == [False, True]
    assert rescored["rescores"][-1]["note"] == "typo in a task"
    assert "typo in a task" in (run / "report.md").read_text()
    # This task file is not the frozen one.
    assert main([*common, "--rescore", "--frozen"]) == 2


def test_a_relay_token_is_sent_as_the_api_key(monkeypatch, isolated_process_state):
    # configure_environment writes the deadline, timeout, signing key, domains and proxy
    # variables; `isolated_process_state` restores the whole environment afterwards, so none
    # of them leaks into later tests (monkeypatch.delenv of an unset name records nothing).
    from evals.run_eval import configure_environment, parse_args

    for name in ("RELAY_TOKEN", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    args = parse_args(["--relay-url", "http://127.0.0.1:8791/v1"])
    configure_environment(args, {"cocktails"})
    assert os.environ["LLM_API_KEY"] == "relay-not-a-secret"
    monkeypatch.setenv("RELAY_TOKEN", "s3cret")
    configure_environment(args, {"cocktails"})
    assert os.environ["LLM_API_KEY"] == "s3cret"
