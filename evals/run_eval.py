"""Run the agent evaluation: every task through the real API, scored, with a report.

The API runs in this process (uvicorn on a free local port), built from the usual settings,
so the whole production path runs: the domain catalogs and recommenders, the chat model over
HTTP, the agent engine, grounding, approvals and the `/chat/stream` endpoint. A LangChain
callback records every model call (offered tools, forced tool, tool calls and text) per turn,
which is how tool selection and arguments are scored.

With a relay (a model without an API key here, answered through files):

    uv run python -m evals.relay --port 8791                       # terminal 1
    uv run python -m evals.run_eval --relay-url http://127.0.0.1:8791/v1 \\
        --model <label> --run-name first-run                         # terminal 2

With a real provider, use the usual LLM_* environment (or `.env`) and leave out
`--relay-url`. Results go to `evals/results/<run-name>/`: `results.jsonl` (one record per
task, with the full trace), `summary.json` (every metric) and `report.md`.
"""

import argparse
import contextvars
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import TypeAdapter

from evals import freeze
from evals.freeze import short
from evals.scoring import score_task, summarize
from evals.tasks import Decision, Task, load_tasks, select

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS = Path(__file__).resolve().parent / "tasks.yaml"
RESULTS = Path(__file__).resolve().parent / "results"
DEFAULT_DEADLINE = 7200.0
DEFAULT_LLM_TIMEOUT = 3600.0
TURN_HEADER = "x-eval-turn"
MAX_HISTORY = 10

CURRENT_TURN: contextvars.ContextVar[str | None] = contextvars.ContextVar("eval_turn", default=None)
logger = logging.getLogger("evals")


# --- Recording model calls ------------------------------------------------------------------


class TurnTag:
    """ASGI middleware: the request's turn tag becomes the context of everything it runs,
    including the model calls of a streamed turn (thread pools copy the context)."""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        tag = None
        if scope["type"] == "http":
            tag = dict(scope.get("headers") or []).get(TURN_HEADER.encode())
        if tag is None:
            await self.app(scope, receive, send)
            return
        token = CURRENT_TURN.set(tag.decode())
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_TURN.reset(token)


def _forced(choice: Any) -> str | None:
    """The tool a `tool_choice` forces: OpenAI {'function': {'name'}}, Anthropic {'name'}."""
    if isinstance(choice, dict):
        return (choice.get("function") or {}).get("name") or choice.get("name")
    return None


class LLMRecorder(BaseCallbackHandler):
    """Records each chat model call under the turn tag of the request that made it."""

    run_inline = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open: dict[Any, dict] = {}
        self._calls: dict[str | None, list[dict]] = defaultdict(list)

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any):
        params = kwargs.get("invocation_params") or {}
        tools = [(t.get("function") or t).get("name") for t in params.get("tools") or []]
        with self._lock:
            self._open[run_id] = {
                "turn": CURRENT_TURN.get(),
                "started": time.monotonic(),
                "tools_offered": tools,
                "forced": _forced(params.get("tool_choice")),
            }

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        message = None
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)
        calls = [{"name": c["name"], "args": c["args"]} for c in getattr(message, "tool_calls", [])]
        calls += [
            {"name": c.get("name"), "args": {}, "invalid": c.get("error") or True}
            for c in getattr(message, "invalid_tool_calls", []) or []
        ]
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        self._finish(run_id, {"tool_calls": calls, "text": content or ""})

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._finish(run_id, {"tool_calls": [], "text": "", "error": type(error).__name__})

    def _finish(self, run_id: Any, result: dict) -> None:
        with self._lock:
            call = self._open.pop(run_id, None)
            if call is None:
                return
            call["seconds"] = round(time.monotonic() - call.pop("started"), 3)
            self._calls[call.pop("turn")].append(call | result)

    def take(self, turn: str) -> list[dict]:
        with self._lock:
            return self._calls.pop(turn, [])


# --- The API in this process ----------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class InProcessAPI:
    """The real app (`curio.app.api.create_app`) under uvicorn in a background thread, with
    every chat model built from settings carrying the recorder."""

    def __init__(self, recorder: LLMRecorder, port: int = 0, start_timeout: float = 1200.0):
        import uvicorn

        from curio.app import services
        from curio.app.api import create_app

        build = services.build_chat_model
        self._restore = lambda: setattr(services, "build_chat_model", build)

        def recorded(settings):
            model = build(settings)
            model.callbacks = [*(model.callbacks or []), recorder]
            return model

        services.build_chat_model = recorded
        app = create_app()
        app.add_middleware(TurnTag)
        self.port = port or _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + start_timeout
        while not self._server.started:
            if not self._thread.is_alive():
                self._restore()
                raise RuntimeError("The API stopped while starting; see api.log in the run folder.")
            if time.monotonic() > deadline:
                raise TimeoutError("The API did not start in time.")
            time.sleep(0.5)

    def close(self) -> None:
        self._server.should_exit = True
        self._thread.join(60)
        self._restore()


# --- Running tasks --------------------------------------------------------------------------


class TaskRunner:
    def __init__(self, client: httpx.Client, recorder: LLMRecorder, served: dict[str, bool]):
        from curio.app.schemas import AppliedEffect, TurnContext

        self.client = client
        self.recorder = recorder
        self.served = served
        self._context_type = TurnContext
        self._effect = TypeAdapter(AppliedEffect)

    def run(self, task: Task) -> dict:
        started = datetime.now(UTC).isoformat(timespec="seconds")
        base = {"task_id": task.id, "domain": task.domain, "category": task.category}
        if not self.served.get(task.domain):
            reason = f"the API does not serve {task.domain} chat"
            return base | {"status": "skipped", "reason": reason, "started": started}
        clock = time.monotonic()
        traces = self._turns(task)
        score = score_task(task, traces)
        return base | {
            "status": score["status"],
            "started": started,
            "seconds": round(time.monotonic() - clock, 2),
            "score": score,
            "turns": traces,
        }

    def _turns(self, task: Task) -> list[dict]:
        from curio.engine.agent import _with_effect  # the client's view of an applied effect

        context = self._context_type.model_validate(task.context)
        history = [h.model_dump(exclude_defaults=True) for h in task.history]
        history_items = self._history_items(task)
        traces: list[dict] = []
        previous: dict | None = None
        for index, spec in enumerate(task.turns):
            if spec.context:
                merged = context.model_dump(mode="json") | spec.context
                context = self._context_type.model_validate(merged)
            payload: dict[str, Any] = {"domain": task.domain, "history": history[-MAX_HISTORY:]}
            payload |= context.model_dump(mode="json", exclude_defaults=True)
            trace: dict[str, Any] = {"index": index, "history_items": history_items}
            if spec.decision is not None:
                pending = (previous or {}).get("pending_action")
                if not pending:
                    trace |= {"kind": "decision", "error": {"detail": "no proposal to decide"}}
                    traces.append(trace)
                    break
                payload["decision"] = decision_payload(pending, spec.decision)
                trace |= {"kind": "decision", "decision": payload["decision"]}
            else:
                payload["message"] = spec.message
                trace |= {"kind": "message", "message": spec.message}
            tag = f"{task.id}#{index}#{uuid.uuid4().hex[:8]}"
            clock = time.monotonic()
            steps, reply, error = self._stream(payload, tag)
            trace |= {
                "seconds": round(time.monotonic() - clock, 2),
                "steps": steps,
                "reply": reply,
                "error": error,
                "llm_calls": self.recorder.take(tag),
            }
            traces.append(trace)
            if reply is None:
                break
            for effect in reply.get("applied") or []:
                context = _with_effect(context, self._effect.validate_python(effect))
            updates = {}
            if reply.get("outfit"):
                updates["outfit"] = reply["outfit"]["state"]
            if reply.get("evening"):
                updates["evening"] = reply["evening"]["state"]
            if updates:
                context = self._context_type.model_validate(
                    context.model_dump(mode="json") | updates
                )
            if spec.message is not None:
                history.append({"role": "user", "content": spec.message})
            history.append(history_item(reply))
            previous = reply
        return traces

    def _stream(self, payload: dict, tag: str) -> tuple[list[dict], dict | None, dict | None]:
        steps: list[dict] = []
        try:
            with self.client.stream(
                "POST", "/chat/stream", json=payload, headers={TURN_HEADER: tag}
            ) as response:
                if response.status_code != 200:
                    response.read()
                    return steps, None, _http_error(response.status_code, response.text)
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("type") == "step":
                        steps.append(event["step"])
                    elif event.get("type") == "reply":
                        return steps, event["reply"], None
                    elif event.get("type") == "error":
                        return steps, None, _http_error(event.get("status"), event.get("detail"))
        except httpx.HTTPError as error:
            return steps, None, {"detail": f"{type(error).__name__}: {error}", "infra": True}
        return steps, None, {"detail": "the stream ended without a reply", "infra": True}

    def _history_items(self, task: Task) -> dict:
        """The catalog rows behind the history's cards, for the grounding audit."""
        items: dict[str, list] = {
            "products": [],
            "cocktails": [],
            "books": [],
            "product_ids": [],
            "drink_ids": [],
            "book_ids": [],
        }
        routes = (
            ("product_ids", "products", "/products/{}"),
            ("drink_ids", "cocktails", "/cocktails/{}"),
            ("book_ids", "books", "/books/{}"),
        )
        for turn in task.history:
            for field, key, route in routes:
                for item_id in getattr(turn, field):
                    items[field].append(item_id)
                    response = self.client.get(route.format(item_id))
                    if response.status_code == 200:
                        items[key].append(response.json())
        return items


def _http_error(status: int | None, detail: Any) -> dict:
    # 409 is the agent's own refusal (an invalid decision); 4xx from the harness and 5xx from
    # the provider or relay say nothing about the agent, so --resume runs those tasks again.
    return {"status": status, "detail": str(detail)[:500], "infra": status != 409}


def decision_payload(pending: dict, decision: Decision) -> dict:
    """The shopper's decision on `pending`: approve it, approve the first N values of each
    list, or decline it."""
    edited = None
    if decision.approve and decision.keep is not None:
        edited = {
            name: value[: decision.keep]
            for name, value in pending.get("args", {}).items()
            if isinstance(value, list) and len(value) > decision.keep
        } or None
    return {"action": pending, "approved": decision.approve, "edited_args": edited}


def history_item(reply: dict) -> dict:
    """An assistant turn as the UI sends it back: its text and the IDs of its cards."""
    item: dict[str, Any] = {"role": "assistant", "content": (reply.get("text") or "")[:4000]}
    for key, field, id_key in (
        ("products", "product_ids", "product_id"),
        ("cocktails", "drink_ids", "drink_id"),
        ("books", "book_ids", "book_id"),
    ):
        if ids := [card[id_key] for card in reply.get(key) or []][:5]:
            item[field] = ids
    return item


# --- Results --------------------------------------------------------------------------------


def read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f} %"


def _ci(interval: list[float] | None) -> str:
    if not interval:
        return ""
    return f", 95 % CI {interval[0] * 100:.0f}-{interval[1] * 100:.0f} %"


def _ratio(count: dict | None) -> str:
    if not count:
        return "n/a"
    return f"{count['passed']}/{count['total']} ({_pct(count['rate'])}{_ci(count.get('ci95'))})"


def _short_ratio(count: dict | None) -> str:
    if not count or not count.get("total"):
        return "n/a"
    return f"{count['passed']}/{count['total']}{_ci(count.get('ci95')).replace(', 95 % CI', ' ·')}"


def slim(summary: dict) -> dict:
    """The parts of a summary the comparison table shows (docs/evals/controls.json)."""
    keys = (
        "tasks",
        "task_success",
        "by_category",
        "by_tag",
        "reliability",
        "safety",
        "injection_resistance",
        "clarification",
        "tool_selection_accuracy",
        "argument_accuracy",
    )
    run = summary.get("run") or {}
    return {key: summary.get(key) for key in keys} | {
        "run": {k: run.get(k) for k in ("run_name", "mode", "model", "commit", "scorer")}
    }


def comparison(summary: dict, others: dict[str, dict]) -> list[str]:
    """This run's key numbers next to other runs' (the controls), with Wilson intervals."""
    if not others:
        return []
    runs = {"this run": summary, **others}
    rows: list[tuple[str, Any]] = [
        ("Task success", lambda s: s.get("task_success")),
        ("Passed, model right first time", lambda s: _get(s, "reliability.all.passed_first_time")),
        (
            "Passed after engine repair",
            lambda s: _get(s, "reliability.all.passed_after_engine_repair"),
        ),
    ]
    for category in (
        "straightforward",
        "factual",
        "follow_up",
        "clarification",
        "approval",
        "decision",
        "adversarial",
    ):
        rows.append((f"Category {category}", lambda s, c=category: _get(s, f"by_category.{c}")))
    for tag in ("safety", "injection", "catalog_gap", "price_trap"):
        rows.append((f"Tag {tag}", lambda s, t=tag: _get(s, f"by_tag.{t}")))
    rows.append(("Safety checks", lambda s: s.get("safety")))
    lines = [
        "## This run next to the controls",
        "",
        "Controls are policies that do nothing useful (docs/agent-eval.md#controls). A task a "
        "control passes measures little; the gap between a model and the best control is the "
        "signal. Cells: passed/total · 95 % Wilson interval.",
        "",
        "| Metric | " + " | ".join(runs) + " |",
        "|---|" + "---|" * len(runs),
    ]
    for label, get in rows:
        lines.append(
            f"| {label} | " + " | ".join(_short_ratio(get(s)) for s in runs.values()) + " |"
        )
    mine = (summary.get("run") or {}).get("scorer")
    stale = [name for name, s in others.items() if (s.get("run") or {}).get("scorer") != mine]
    if stale:
        lines += [
            "",
            f"Scored with a different task file or scorer than this run: {', '.join(stale)}. "
            "Their numbers are not comparable until they are run again.",
        ]
    return [*lines, ""]


def _get(data: dict, path: str) -> Any:
    for part in path.split("."):
        data = (data or {}).get(part)
    return data


def report(summary: dict, records: list[dict], tasks: list[Task]) -> str:
    """The run's report in Markdown."""
    s = summary
    run = defaultdict(lambda: "?", summary.get("run") or {})
    rel = s["reliability"]
    frozen = run["frozen"]
    frozen_text = "yes (matched evals/FROZEN.json)" if frozen is True else "no"
    lines = [
        f"# Agent evaluation: {run['run_name']}",
        "",
        f"- Mode: {run['mode']}; provider `{run['provider']}`, model `{run['model']}`, "
        f"temperature {run['temperature']}",
        f"- Turn deadline {run['turn_deadline_seconds']} s and model-call timeout "
        f"{run['llm_timeout_seconds']} s (production defaults: 90 s and 60 s)",
        f"- Tasks file `{run['tasks_file']}`, commit `{run['commit']}`, "
        f"{run['started']} to {run['finished']}",
        f"- Scorer: {short(run.get('scorer'))}",
        f"- Frozen run: {frozen_text}",
        f"- Tasks: {s['tasks']['scored']} scored of {s['tasks']['selected']} selected; "
        f"errors {len(s['tasks']['errors'])}, skipped {len(s['tasks']['skipped'])}, "
        f"not run {len(s['tasks']['not_run'])}",
        "",
        "## Metrics",
        "",
        "Rates carry 95 % Wilson intervals. One run of each task: the intervals are about "
        "sampling the task set, not the model's run-to-run variance.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Task success | {_ratio(s['task_success'])} |",
        f"| Passed, model right first time | {_ratio(rel['all']['passed_first_time'])} |",
        f"| Passed after the engine caught and repaired a draft | "
        f"{_ratio(rel['all']['passed_after_engine_repair'])} |",
        f"| Factual tasks passed, right first time | "
        f"{_ratio(rel['factual']['passed_first_time'])} |",
        f"| Factual tasks passed after engine repair | "
        f"{_ratio(rel['factual']['passed_after_engine_repair'])} |",
        f"| Turns whose first draft passed grounding | {_ratio(rel['turns']['first_pass'])} |",
        f"| Turns caught by grounding and repaired | "
        f"{_ratio(rel['turns']['caught_and_repaired'])} |",
        f"| Turns caught by grounding, then fallback | "
        f"{_ratio(rel['turns']['caught_then_fallback'])} |",
        f"| Tool selection | {_ratio(s['tool_selection_accuracy'])} |",
        f"| Arguments | {_ratio(s['argument_accuracy'])} |",
        f"| Final status | {_ratio(s['status_accuracy'])} |",
        f"| Grounding: first-pass verified | {_ratio(s['grounding']['first_pass_verified'])} |",
        f"| Grounding: repaired | {_ratio(s['grounding']['repaired'])} |",
        f"| Grounding: fallback | {_ratio(s['grounding']['fallback'])} |",
        f"| Grounding outcomes | {s['grounding']['outcomes']} |",
        f"| Grounding: suspects slipped through (audit) | {s['grounding']['slipped_through']} |",
        f"| Proposals when required | "
        f"{_ratio(s['approval_discipline']['proposals_when_required'])} |",
        f"| No claim before approval | "
        f"{_ratio(s['approval_discipline']['no_claim_before_approval'])} |",
        f"| Clarification precision / recall | {_pct(s['clarification']['precision'])}"
        f"{_ci(s['clarification'].get('precision_ci95'))} / "
        f"{_pct(s['clarification']['recall'])}{_ci(s['clarification'].get('recall_ci95'))} |",
        f"| Safety checks | {_ratio(s['safety'])} |",
        f"| Injection resistance | {_ratio(s['injection_resistance'])} |",
        f"| Text checks | {_ratio(s['text_checks'])} |",
        f"| Card checks | {_ratio(s['card_checks'])} |",
        f"| Tool calls per turn (mean / max) | {s['tool_calls_per_turn']['mean']} / "
        f"{s['tool_calls_per_turn']['max']} |",
        f"| Turns hitting a budget | {s['budget_hits']['turns']}/{s['budget_hits']['of']} |",
        f"| Latency per turn (mean / median / max s) | {s['latency_seconds']['mean']} / "
        f"{s['latency_seconds']['median']} / {s['latency_seconds']['max']} |",
        "",
        f"Latency: {s['latency_seconds']['note']}",
        "",
        *comparison(s, s.get("comparison") or {}),
        "## By domain and category",
        "",
        "| Group | Passed |",
        "|---|---|",
    ]
    for name, count in {**s["by_domain"], **s["by_category"]}.items():
        lines.append(f"| {name} | {_ratio(count)} |")
    lines += ["", "## Tasks", "", "| Task | Domain | Category | Result | Failed checks |"]
    lines.append("|---|---|---|---|---|")
    latest = {r["task_id"]: r for r in records}
    for task in tasks:
        record = latest.get(task.id)
        if record is None:
            lines.append(f"| {task.id} | {task.domain} | {task.category} | not run | |")
            continue
        failed = [
            f"turn {i + 1}: {c['name']}" + (f" ({c['detail']})" if c["detail"] else "")
            for i, turn in enumerate((record.get("score") or {}).get("turns", []))
            for c in turn["checks"]
            if not c["passed"]
        ]
        cell = "<br>".join(_cell(f) for f in failed) or record.get("reason", "")
        result = record["status"]
        if result == "passed" and (record.get("score") or {}).get("engine_caught"):
            result = "passed (engine repaired)"
        lines.append(f"| {task.id} | {task.domain} | {task.category} | {result} | {cell} |")
    if s["grounding"]["audit_suspects"]:
        lines += ["", "## Grounding audit suspects (review by hand)", ""]
        for item in s["grounding"]["audit_suspects"]:
            lines.append(f"- {item['task']}: {', '.join(item['suspects'])}")
    if s["budget_hits"]["details"]:
        lines += ["", "## Budget hits", ""]
        lines += [f"- {b['task']}: {b['budget']}" for b in s["budget_hits"]["details"]]
    if run["rescores"] != "?" and run["rescores"]:
        lines += ["", "## Rescores", ""]
        for entry in run["rescores"]:
            changed = "scorer changed" if entry.get("changed") else "same scorer"
            note = entry.get("note") or "no note"
            lines.append(f"- {entry.get('at')}: {changed}; {note}")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:300]


def _commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


# --- CLI ------------------------------------------------------------------------------------


def configure_environment(args: argparse.Namespace, domains: set[str]) -> None:
    """LLM, deadline and domain settings for the in-process API, before it reads them."""
    env = os.environ
    if args.relay_url:
        env["LLM_PROVIDER"] = "openai"
        env["LLM_BASE_URL"] = args.relay_url
        # The relay's bearer token when it requires one (RELAY_TOKEN), sent as the API key.
        env["LLM_API_KEY"] = env.get("RELAY_TOKEN") or "relay-not-a-secret"
        env["LLM_MODEL"] = args.model or "relay"
    elif args.model:
        env["LLM_MODEL"] = args.model
    if args.provider:
        env["LLM_PROVIDER"] = args.provider
    env["AGENT_TURN_DEADLINE_SECONDS"] = str(args.turn_deadline)
    env["LLM_TIMEOUT_SECONDS"] = str(args.llm_timeout)
    env.setdefault("AGENT_SIGNING_KEY", secrets.token_hex(32))
    env["ENABLED_DOMAINS"] = ",".join(sorted(domains))
    if args.data_dir:
        env["DATA_DIR"] = str(args.data_dir)
    if args.qdrant_path:
        env["QDRANT_PATH"] = str(args.qdrant_path)
    local = "127.0.0.1,localhost"
    for name in ("NO_PROXY", "no_proxy"):
        env[name] = f"{env[name]},{local}" if env.get(name) else local


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_eval", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument(
        "--only", action="append", help="Task ids or domains, comma-separated or repeated."
    )
    parser.add_argument("--run-name", default=None, help="Default: run-<UTC timestamp>.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--resume", action="store_true", help="Skip tasks already scored in this run's results."
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Score this run's saved traces again and rewrite its summary and report.",
    )
    parser.add_argument(
        "--relay-url", help="An OpenAI-compatible relay, e.g. http://127.0.0.1:8791/v1."
    )
    parser.add_argument("--model", help="LLM_MODEL (with a relay: a label for the report).")
    parser.add_argument("--provider", help="LLM_PROVIDER (default: from the environment).")
    parser.add_argument(
        "--turn-deadline",
        type=float,
        default=DEFAULT_DEADLINE,
        help="AGENT_TURN_DEADLINE_SECONDS for this run (default: %(default)s).",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=DEFAULT_LLM_TIMEOUT,
        help="LLM_TIMEOUT_SECONDS for this run (default: %(default)s).",
    )
    parser.add_argument(
        "--frozen",
        action="store_true",
        help="Refuse to run or rescore unless the task file and scorer match evals/FROZEN.json.",
    )
    parser.add_argument(
        "--rescore-note",
        default="",
        help="Why a run is rescored with a changed task file or scorer (required then; "
        "recorded in run.json and the report).",
    )
    parser.add_argument(
        "--compare",
        action="append",
        help="Runs to show next to this one: run names in the results folder or paths to a "
        "summary.json or a controls.json (default: docs/evals/controls.json if present).",
    )
    parser.add_argument("--port", type=int, default=0, help="API port (default: a free one).")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--qdrant-path", type=Path)
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    return args


CONTROLS = ROOT / "docs" / "evals" / "controls.json"


def load_comparison(names: list[str] | None, results_dir: Path, own: str) -> dict[str, dict]:
    """The runs to compare with, by name: from `--compare`, else the committed controls."""
    found: dict[str, dict] = {}
    items = [n.strip() for item in names or [] for n in item.split(",") if n.strip()]
    if not items and CONTROLS.is_file():
        items = [str(CONTROLS)]
    for item in items:
        path = Path(item)
        if not path.exists():
            path = results_dir / item / "summary.json"
        if not path.is_file():
            raise FileNotFoundError(f"--compare: no run or file {item}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if "task_success" in data:  # one run's summary
            name = (data.get("run") or {}).get("run_name") or path.parent.name
            found[name] = slim(data)
        else:  # controls.json: {name: slim summary}
            found |= data
    found.pop(own, None)
    return found


def finish(
    run_dir: Path,
    tasks: list[Task],
    records: list[dict],
    run: dict,
    compare: dict[str, dict] | None = None,
) -> dict:
    """Write `summary.json` and `report.md` for the records; return the summary."""
    summary = summarize(tasks, records, relayed=run.get("mode") == "relay")
    summary["run"] = run
    summary["comparison"] = compare or {}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    (run_dir / "report.md").write_text(report(summary, records, tasks), encoding="utf-8")
    print(
        f"Task success {summary['tasks']['passed']}/{summary['tasks']['scored']}; "
        f"report: {run_dir / 'report.md'}",
        flush=True,
    )
    return summary


def rescore(
    run_dir: Path,
    tasks: list[Task],
    current: dict,
    note: str = "",
    compare: dict[str, dict] | None = None,
) -> int:
    """Score a finished run's saved traces again. With a changed task file or scorer this
    needs a note, which is recorded: a verdict never changes silently after a run is seen."""
    results_path = run_dir / "results.jsonl"
    if not results_path.is_file():
        print(f"No {results_path} to rescore.")
        return 2
    run = {}
    for name, key in (("run.json", None), ("summary.json", "run")):
        if not run and (path := run_dir / name).is_file():
            data = json.loads(path.read_text())
            run = data.get(key, {}) if key else data
    changed = run.get("scorer") != current
    if changed and not note.strip():
        print(
            "The task file or scorer differs from the one this run was scored with "
            f"({short(run.get('scorer'))} -> {short(current)}). Rescoring would change its "
            "verdicts after they were seen: pass --rescore-note explaining why."
        )
        return 2
    by_id = {task.id: task for task in tasks}
    records = []
    for record in read_records(results_path):
        task = by_id.get(record["task_id"])
        if task is not None and record.get("turns") is not None:
            record = record | {"score": (score := score_task(task, record["turns"]))}
            record["status"] = score["status"]
        records.append(record)
    with results_path.open("w", encoding="utf-8") as out:
        out.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    entry = {"at": _now(), "changed": changed, "note": note.strip(), "scorer": current}
    if changed:
        entry["previous_scorer"] = run.get("scorer")
    run = run | {
        "rescored": entry["at"],
        "rescores": [*run.get("rescores", []), entry],
        "scorer": current,
        "frozen": run.get("frozen", False) and not changed,
    }
    (run_dir / "run.json").write_text(json.dumps(run, indent=1))
    finish(run_dir, tasks, records, run, compare)
    return 0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tasks = select(load_tasks(args.tasks), args.only)
    run_name = args.run_name or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    run_dir = args.results_dir / run_name
    current = freeze.fingerprint(args.tasks)
    if args.frozen and (problems := freeze.differences(current, freeze.read_frozen())):
        print("Not the frozen task file and scorer (evals/FROZEN.json):")
        print("\n".join(f"  - {p}" for p in problems))
        return 2
    compare = load_comparison(args.compare, args.results_dir, run_name)
    if args.rescore:
        return rescore(run_dir, tasks, current, args.rescore_note, compare)
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    if not args.resume and results_path.exists():
        print(f"{results_path} exists: pass --resume to continue it, or another --run-name.")
        return 2
    records = read_records(results_path) if args.resume else []
    done = {r["task_id"] for r in records if r["status"] in ("passed", "failed")}
    todo = [task for task in tasks if task.id not in done]
    configure_environment(args, {task.domain for task in tasks})
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(run_dir / "api.log", encoding="utf-8")],
        force=True,
    )
    from curio.app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if not settings.llm_configured:
        print("No model: pass --relay-url, or set LLM_MODEL (and the provider's LLM_* settings).")
        return 2
    run_file = run_dir / "run.json"
    earlier = json.loads(run_file.read_text()) if args.resume and run_file.is_file() else {}
    if earlier.get("scorer") and earlier["scorer"] != current:
        print(
            "This run was started with another task file or scorer; --resume would mix "
            "verdicts. Rescore it first (--rescore --rescore-note ...) or start a new run."
        )
        return 2
    run = {
        "run_name": run_name,
        "mode": "relay" if args.relay_url else "provider",
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "temperature": settings.llm_temperature,
        # The run's long budgets, recorded: production uses 90 s per turn and 60 s per call.
        "turn_deadline_seconds": settings.agent_turn_deadline_seconds,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "long_deadline": settings.agent_turn_deadline_seconds > 90,
        "concurrency": args.concurrency,
        "tasks_file": freeze.relative(args.tasks),
        # What scored this run: the scorer version and the hashes of the task file, the
        # scorer and the task schema; "frozen" when they matched evals/FROZEN.json.
        "scorer": current,
        "frozen": bool(args.frozen) and earlier.get("frozen", True),
        "selected": [task.id for task in tasks],
        "commit": _commit(),
        "started": earlier.get("started", _now()),
        "sessions": [*earlier.get("sessions", []), _now()],
    }
    run_file.write_text(json.dumps(run, indent=1))
    print(f"{len(todo)} of {len(tasks)} tasks to run; results in {run_dir}", flush=True)
    recorder = LLMRecorder()
    api = InProcessAPI(recorder, args.port)
    lock = threading.Lock()
    try:
        timeout = httpx.Timeout(connect=30, read=args.llm_timeout * 2 + 120, write=60, pool=None)
        with httpx.Client(base_url=api.url, timeout=timeout, trust_env=False) as client:
            health = client.get("/health").json()
            served = {d["id"]: d["chat_enabled"] for d in health["domains"]}
            runner = TaskRunner(client, recorder, served)
            with (
                results_path.open("a", encoding="utf-8") as out,
                ThreadPoolExecutor(args.concurrency) as pool,
            ):
                futures = {pool.submit(runner.run, task): task for task in todo}
                for count, future in enumerate(as_completed(futures), start=1):
                    task = futures[future]
                    try:
                        record = future.result()
                    except Exception as error:  # a harness bug: record it, keep going
                        logger.exception("Task %s crashed", task.id)
                        record = {
                            "task_id": task.id,
                            "domain": task.domain,
                            "category": task.category,
                            "status": "error",
                            "reason": f"{type(error).__name__}: {error}",
                        }
                    with lock:
                        out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        out.flush()
                        records.append(record)
                    took = f", {record['seconds']:.0f} s" if record.get("seconds") else ""
                    print(f"[{count}/{len(todo)}] {task.id}: {record['status']}{took}", flush=True)
    finally:
        api.close()
    run["finished"] = _now()
    run_file.write_text(json.dumps(run, indent=1))
    finish(run_dir, tasks, records, run, compare)
    return 0


if __name__ == "__main__":
    sys.exit(main())
