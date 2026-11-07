"""Answer relay requests: list them, read one, and write a validated answer.

The relay (`python -m evals.relay`) queues each chat request the app makes as a file. This
CLI is how a model agent answers them:

    uv run python -m evals.relay_answer wait              # block until a request is pending
    uv run python -m evals.relay_answer list              # pending requests, oldest first
    uv run python -m evals.relay_answer show              # the oldest pending request
    uv run python -m evals.relay_answer show 000012       # one request
    uv run python -m evals.relay_answer answer 000012 --text "Here are two drinks ..."
    uv run python -m evals.relay_answer answer 000012 --tool SearchCocktails '{"query": "sour"}'
    uv run python -m evals.relay_answer answer 000012 --tool-calls '[{"name": "A", ...}]'
    uv run python -m evals.relay_answer answer 000012 --text-file answer.md
    uv run python -m evals.relay_answer answer 000012 --text-file - <<'EOF'
    It's the **Whiskey Sour** (contains alcohol) ...
    EOF

`show` prints the system prompt, the conversation (with earlier tool calls and their
results), each offered tool with its JSON schema, the tool choice and any response format.
`answer` checks before writing: the request is still pending, tool names are offered,
arguments are a JSON object that fits the tool's schema, a forced tool choice is obeyed
(structured-output calls such as the fashion intent extractor force one function), and a
`json_schema` response format gets JSON text that fits its schema. Nothing is written when
a check fails; the error says what to fix.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from evals.relay import DEFAULT_QUEUE, RelayQueue
from evals.schema_check import validate


class AnswerError(ValueError):
    """An answer that does not fit its request; nothing was written."""


# --- Reading requests -----------------------------------------------------------------------


def _text(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text") or f"[{part.get('type', 'part')}]")
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return "" if content is None else str(content)


def tools_of(request: dict) -> dict[str, dict]:
    """The offered functions by name: {'description', 'parameters'}."""
    found = {}
    for tool in request.get("tools") or []:
        function = tool.get("function", tool)
        if "name" in function:
            found[function["name"]] = {
                "description": function.get("description", ""),
                "parameters": function.get("parameters") or {"type": "object"},
            }
    return found


def forced_tool(request: dict) -> str | None:
    """The function a request forces the model to call, if any."""
    choice = request.get("tool_choice")
    if isinstance(choice, dict):
        return (choice.get("function") or {}).get("name") or choice.get("name")
    return None


def response_schema(request: dict) -> tuple[str | None, dict | None]:
    """The response format's type ('json_schema', 'json_object') and schema, if any."""
    fmt = request.get("response_format")
    if not isinstance(fmt, dict):
        return None, None
    kind = fmt.get("type")
    if kind == "json_schema":
        spec = fmt.get("json_schema") or {}
        return kind, spec.get("schema") or {}
    return kind, None


def _compact_schema(schema: Any) -> Any:
    """The schema without titles, which only repeat the names."""
    if isinstance(schema, dict):
        return {k: _compact_schema(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_compact_schema(v) for v in schema]
    return schema


def summary(seq: str, request: dict) -> str:
    """One line per request for `list`."""
    messages = request.get("messages") or []
    if forced := forced_tool(request):
        kind = f"structured output: call {forced}"
    elif response_schema(request)[0]:
        kind = f"structured output: {response_schema(request)[0]}"
    elif messages and messages[-1].get("role") == "tool":
        kind = "after a tool result"
    else:
        kind = "agent turn"
    last_user = next(
        (_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    last_user = " ".join(last_user.split())
    return f"{seq}  [{kind}]  {len(tools_of(request))} tools  user: {last_user[:90]}"


def render(seq: str, request: dict, max_chars: int = 0) -> str:
    """A request in a compact, readable form for the model that answers it."""

    def clip(text: str) -> str:
        if max_chars and len(text) > max_chars:
            return text[:max_chars] + f" … [{len(text) - max_chars} more characters]"
        return text

    lines = [f"=== Relay request {seq} ==="]
    settings = {k: request[k] for k in ("model", "temperature") if k in request}
    if settings:
        lines.append("Settings: " + ", ".join(f"{k} {v}" for k, v in settings.items()))
    forced = forced_tool(request)
    choice = request.get("tool_choice")
    if forced:
        lines.append(f"Tool choice: you MUST answer with exactly one call to {forced}.")
    elif choice in ("required", "none"):
        lines.append(f"Tool choice: {choice}.")
    kind, schema = response_schema(request)
    if kind:
        lines.append(f"Response format: {kind}; answer with JSON text only.")
        if schema:
            lines.append("Response schema: " + json.dumps(_compact_schema(schema)))
    for message in request.get("messages") or []:
        role = message.get("role", "?")
        if role == "tool":
            lines.append(f"--- [tool result for {message.get('tool_call_id', '?')}]")
        else:
            lines.append(f"--- [{role}]")
        if text := _text(message.get("content")).strip():
            lines.append(clip(text))
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            lines.append(
                f"(tool call {call.get('id', '?')}) {function.get('name')} "
                f"{function.get('arguments', '{}')}"
            )
    tools = tools_of(request)
    if tools:
        lines.append(f"=== Tools you may call ({len(tools)}) ===")
        for name, tool in tools.items():
            description = " ".join(str(tool["description"]).split())
            lines.append(f"* {name}: {description}")
            lines.append("  parameters: " + json.dumps(_compact_schema(tool["parameters"])))
    lines.append("=== How to answer ===")
    if forced:
        lines.append(
            f"uv run python -m evals.relay_answer answer {seq} --tool {forced} '<JSON arguments>'"
        )
    else:
        lines.append(f"uv run python -m evals.relay_answer answer {seq} --text '<your reply>'")
        lines.append(
            f"uv run python -m evals.relay_answer answer {seq} --text-file - <<'EOF' "
            "(for text with quotes; end with EOF)"
        )
        if tools:
            lines.append(
                f"uv run python -m evals.relay_answer answer {seq} --tool <Name> '<JSON arguments>'"
                "  (repeat --tool for several calls)"
            )
    return "\n".join(lines)


# --- Writing answers ------------------------------------------------------------------------


def parse_arguments(name: str, raw: Any) -> dict:
    """Tool arguments as a dict, from a JSON string or an already parsed object."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as error:
            raise AnswerError(f"Arguments for {name} are not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise AnswerError(f"Arguments for {name} must be a JSON object, got {type(raw).__name__}")
    return raw


def build_answer(
    request: dict, text: str | None = None, tool_calls: list[dict] | None = None
) -> dict:
    """A checked response for `request`: raises AnswerError when it does not fit."""
    tool_calls = tool_calls or []
    text = text if text is None else text.strip()
    if not text and not tool_calls:
        raise AnswerError("Give a text answer or at least one tool call.")
    offered = tools_of(request)
    forced = forced_tool(request)
    choice = request.get("tool_choice")
    if forced and (len(tool_calls) != 1 or tool_calls[0].get("name") != forced or text):
        raise AnswerError(
            f"This request forces one call to {forced} and nothing else: answer with "
            f"--tool {forced} '<JSON arguments>'."
        )
    if choice == "required" and not tool_calls:
        raise AnswerError("This request requires a tool call.")
    if choice == "none" and tool_calls:
        raise AnswerError("This request allows no tool calls; answer with text.")
    if tool_calls and request.get("parallel_tool_calls") is False and len(tool_calls) > 1:
        raise AnswerError("This request allows only one tool call at a time.")
    checked = []
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            raise AnswerError(f"Tool call {index + 1} needs a 'name'.")
        name = call["name"]
        if name not in offered:
            names = ", ".join(offered) or "none"
            raise AnswerError(f"Unknown tool {name!r}; this request offers: {names}.")
        arguments = parse_arguments(name, call.get("arguments", {}))
        parameters = offered[name]["parameters"]
        if errors := validate(arguments, parameters):
            raise AnswerError(f"Arguments for {name} do not fit its schema: " + "; ".join(errors))
        entry = {"name": name, "arguments": arguments}
        if call.get("id"):
            entry["id"] = str(call["id"])
        checked.append(entry)
    kind, schema = response_schema(request)
    if text and kind in ("json_schema", "json_object"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise AnswerError(f"This request wants JSON text: {error}") from error
        if kind == "json_object" and not isinstance(value, dict):
            raise AnswerError("This request wants a JSON object.")
        if schema and (errors := validate(value, schema)):
            raise AnswerError("The JSON does not fit the response schema: " + "; ".join(errors))
    answer: dict[str, Any] = {"content": text or None}
    if checked:
        answer["tool_calls"] = checked
    return answer


def answer(
    queue: RelayQueue,
    seq: str,
    text: str | None = None,
    tool_calls: list[dict] | None = None,
    *,
    force: bool = False,
) -> dict:
    """Check and write the answer to request `seq`."""
    seq = queue.normalise(seq)
    state = queue.state(seq)
    if state == "unknown":
        raise AnswerError(f"No relay request {seq} in {queue.root}.")
    if state == "expired":
        raise AnswerError(f"Request {seq} expired: the relay stopped waiting for it.")
    if state == "answered" and not force:
        raise AnswerError(f"Request {seq} is already answered (use --force to replace it).")
    response = build_answer(queue.read_request(seq), text, tool_calls)
    queue.write_response(seq, response)
    return response


def _tool_calls_from_args(args: argparse.Namespace) -> list[dict]:
    calls: list[dict] = []
    for name, raw in args.tool or []:
        calls.append({"name": name, "arguments": parse_arguments(name, raw)})
    if args.tool_calls is not None:
        try:
            listed = json.loads(args.tool_calls)
        except json.JSONDecodeError as error:
            raise AnswerError(f"--tool-calls is not valid JSON: {error}") from error
        if isinstance(listed, dict):
            listed = [listed]
        if not isinstance(listed, list):
            raise AnswerError("--tool-calls must be a JSON list of {name, arguments} objects.")
        calls.extend(listed)
    return calls


def _read(path: Path) -> str:
    """A file's text, or standard input for "-"."""
    return sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")


def _answer_from_json_file(path: Path) -> tuple[str | None, list[dict]]:
    try:
        data = json.loads(_read(path))
    except json.JSONDecodeError as error:
        raise AnswerError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise AnswerError(f"{path} must hold an object with 'content' and/or 'tool_calls'.")
    return data.get("content"), list(data.get("tool_calls") or [])


# --- CLI ------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.relay_answer",
        description="List, read and answer relay requests.",
    )
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Pending requests, oldest first.")
    listing.add_argument("--all", action="store_true", help="Also answered and expired ones.")
    show = commands.add_parser("show", help="Print one request (default: the oldest pending).")
    show.add_argument("seq", nargs="?")
    show.add_argument(
        "--max-chars", type=int, default=0, help="Clip long messages (default: show all)."
    )
    show.add_argument("--raw", action="store_true", help="Print the request JSON as stored.")
    wait = commands.add_parser("wait", help="Block until a request is pending; print its seq.")
    wait.add_argument("--timeout", type=float, default=600.0)
    wait.add_argument("--show", action="store_true", help="Also print the request.")
    reply = commands.add_parser("answer", help="Write a checked answer to one request.")
    reply.add_argument("seq")
    reply.add_argument("--text", help="The assistant's text answer.")
    reply.add_argument(
        "--text-file", type=Path, help="Read the text answer from a file ('-': standard input)."
    )
    reply.add_argument(
        "--tool",
        nargs=2,
        action="append",
        metavar=("NAME", "JSON_ARGS"),
        help="One tool call; repeat for several.",
    )
    reply.add_argument("--tool-calls", help='JSON list: [{"name": ..., "arguments": {...}}].')
    reply.add_argument(
        "--json-file",
        type=Path,
        help="A file ('-': standard input) with {'content': ..., 'tool_calls': [...]}.",
    )
    reply.add_argument("--force", action="store_true", help="Replace an existing answer.")
    args = parser.parse_args(argv)
    queue = RelayQueue(args.queue)
    try:
        if args.command == "list":
            sequences = queue.sequences() if args.all else queue.pending()
            if not sequences:
                print("No pending requests." if not args.all else "No requests.")
            for seq in sequences:
                line = summary(seq, queue.read_request(seq))
                print(line if not args.all else f"{line}  ({queue.state(seq)})")
        elif args.command == "show":
            seq = queue.normalise(args.seq) if args.seq else next(iter(queue.pending()), None)
            if seq is None:
                print("No pending requests.")
                return 0
            if queue.state(seq) == "unknown":
                raise AnswerError(f"No relay request {seq} in {queue.root}.")
            request = queue.read_request(seq)
            if args.raw:
                print(json.dumps(request, indent=1, ensure_ascii=False))
            else:
                if (state := queue.state(seq)) != "pending":
                    print(f"(request {seq} is {state})")
                print(render(seq, request, args.max_chars))
        elif args.command == "wait":
            deadline = time.monotonic() + args.timeout
            while not (pending := queue.pending()):
                if time.monotonic() > deadline:
                    print("No pending requests.")
                    return 3
                time.sleep(0.5)
            print(pending[0])
            if args.show:
                print(render(pending[0], queue.read_request(pending[0])))
        else:
            text = args.text
            if args.text_file is not None:
                text = _read(args.text_file)
            calls = _tool_calls_from_args(args)
            if args.json_file is not None:
                file_text, file_calls = _answer_from_json_file(args.json_file)
                text = text if text is not None else file_text
                calls.extend(file_calls)
            written = answer(queue, args.seq, text, calls, force=args.force)
            names = [c["name"] for c in written.get("tool_calls", [])]
            parts = (["text"] if written.get("content") else []) + names
            print(f"Answered {queue.normalise(args.seq)}: {', '.join(parts)}.")
    except AnswerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
