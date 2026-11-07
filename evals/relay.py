"""An OpenAI-compatible chat completions server that relays every request through files.

It lets the agent evaluation run on a model that has no API key in this environment: the
app talks to the relay exactly as to a provider (LLM_PROVIDER=openai, LLM_BASE_URL pointing
here), and each request is answered by whoever reads the queue, typically a model agent
using `python -m evals.relay_answer`.

For each POST /v1/chat/completions the relay writes `<queue>/<seq>.request.json` (the full
request body: model, messages, tools, tool_choice, response_format, temperature, ...) and
blocks until `<seq>.response.json` appears, then returns it as a `chat.completion` with the
assistant's `content` and/or `tool_calls`, a `finish_reason` and zero `usage`. A request
not answered within `--timeout` seconds gets HTTP 504 and an `<seq>.expired` marker, so it
leaves the pending list. Every file is written atomically (a temporary file, then a rename).

A response file holds `{"content": "text"}` or `{"tool_calls": [{"name": "Tool",
"arguments": {...}}]}` (ids are optional and generated when missing), or both.

Run it:

    uv run python -m evals.relay --port 8791 --queue evals/relay_queue
"""

import argparse
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_QUEUE = Path(__file__).resolve().parent / "relay_queue"
DEFAULT_TIMEOUT = 3600.0
SEQ_WIDTH = 6
_SEQ_FILE = re.compile(r"^(\d+)\.request\.json$")


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON to `path` so a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=1)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class RelayQueue:
    """The relay's folder: numbered request files and the response written for each."""

    root: Path

    def request_path(self, seq: str) -> Path:
        return self.root / f"{seq}.request.json"

    def response_path(self, seq: str) -> Path:
        return self.root / f"{seq}.response.json"

    def expired_path(self, seq: str) -> Path:
        return self.root / f"{seq}.expired"

    def sequences(self) -> list[str]:
        if not self.root.is_dir():
            return []
        found = (m.group(1) for p in self.root.iterdir() if (m := _SEQ_FILE.match(p.name)))
        return sorted(found, key=int)

    def pending(self) -> list[str]:
        """Requests still waiting for an answer, oldest first."""
        return [s for s in self.sequences() if self.state(s) == "pending"]

    def state(self, seq: str) -> str:
        if not self.request_path(seq).is_file():
            return "unknown"
        if self.response_path(seq).is_file():
            return "answered"
        if self.expired_path(seq).exists():
            return "expired"
        return "pending"

    def normalise(self, seq: str | int) -> str:
        return str(int(seq)).zfill(SEQ_WIDTH)

    def read_request(self, seq: str) -> dict:
        return json.loads(self.request_path(seq).read_text(encoding="utf-8"))

    def read_response(self, seq: str) -> dict:
        return json.loads(self.response_path(seq).read_text(encoding="utf-8"))

    def write_response(self, seq: str, response: dict) -> None:
        write_json_atomic(self.response_path(seq), response)


class Relay:
    """Numbers requests, queues them as files and waits for their answers."""

    def __init__(self, queue: RelayQueue, timeout: float = DEFAULT_TIMEOUT, poll: float = 0.2):
        self.queue = queue
        self.timeout = timeout
        self.poll = poll
        self._lock = threading.Lock()
        existing = queue.sequences()
        self._next = int(existing[-1]) + 1 if existing else 1

    def submit(self, body: dict) -> str:
        with self._lock:
            seq = self.queue.normalise(self._next)
            self._next += 1
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        write_json_atomic(
            self.queue.request_path(seq), {"relay": {"seq": seq, "at": stamp}, **body}
        )
        return seq

    def wait(self, seq: str) -> dict | None:
        """The answer to `seq`, or None when it did not come within the timeout."""
        deadline = time.monotonic() + self.timeout
        path = self.queue.response_path(seq)
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    return self.queue.read_response(seq)
                except json.JSONDecodeError:
                    pass  # only possible for a hand-written, non-atomic file; retry
            time.sleep(self.poll)
        self.queue.expired_path(seq).write_text(stamp_now())
        return None


def stamp_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def completion(seq: str, model: str, answer: dict) -> dict:
    """An OpenAI `chat.completion` for a relay answer."""
    message: dict[str, Any] = {"role": "assistant", "content": answer.get("content")}
    calls = []
    for index, call in enumerate(answer.get("tool_calls") or []):
        arguments = call.get("arguments", {})
        calls.append(
            {
                "id": call.get("id") or f"call_{seq}_{index + 1}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": arguments
                    if isinstance(arguments, str)
                    else json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    if calls:
        message["tool_calls"] = calls
    return {
        "id": f"chatcmpl-relay-{seq}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
                "logprobs": None,
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_chunks(payload: dict) -> list[dict]:
    """The same completion as `chat.completion.chunk` events, for `stream: true` requests."""
    choice = payload["choices"][0]
    message = choice["message"]
    delta: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
    base = {k: payload[k] for k in ("id", "created", "model")} | {"object": "chat.completion.chunk"}
    return [
        base | {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        base
        | {"choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}]}
        | {"usage": payload["usage"]},
    ]


def _handler(relay: Relay) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/models"):
                self._json(200, {"object": "list", "data": [{"id": "relay", "object": "model"}]})
            elif self.path.rstrip("/") in ("", "/health", "/v1"):
                self._json(200, {"status": "ok", "pending": len(relay.queue.pending())})
            else:
                self._json(404, _error("Not found", "not_found"))

        def do_POST(self) -> None:
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._json(404, _error("Only /v1/chat/completions is relayed", "not_found"))
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                self._json(400, _error("The request body is not JSON", "invalid_request_error"))
                return
            seq = relay.submit(body)
            answer = relay.wait(seq)
            if answer is None:
                message = f"No answer to relay request {seq} within {relay.timeout:.0f} s"
                self._json(504, _error(message, "timeout"))
                return
            payload = completion(seq, str(body.get("model", "relay")), answer)
            try:
                if body.get("stream"):
                    self._stream(stream_chunks(payload))
                else:
                    self._json(200, payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up waiting; the answer stays in the queue

        def _json(self, status: int, data: dict) -> None:
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _stream(self, chunks: list[dict]) -> None:
            raw = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            data = raw.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def _error(message: str, kind: str) -> dict:
    return {"error": {"message": message, "type": kind, "code": kind}}


def serve(
    port: int,
    queue: Path = DEFAULT_QUEUE,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    host: str = "127.0.0.1",
    poll: float = 0.2,
) -> ThreadingHTTPServer:
    """Start the relay on `host:port` in a daemon thread; call `.shutdown()` to stop it."""
    relay = Relay(RelayQueue(Path(queue)), timeout, poll)
    server = ThreadingHTTPServer((host, port), _handler(relay))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Seconds to wait for each answer before HTTP 504 (default: %(default)s).",
    )
    args = parser.parse_args(argv)
    server = serve(args.port, args.queue, args.timeout, host=args.host)
    print(
        f"Relay on http://{args.host}:{args.port}/v1, queue {args.queue.resolve()}, "
        f"timeout {args.timeout:.0f} s per request",
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
