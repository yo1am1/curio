"""`scripts/quickstart.sh` with a recording `uv`: where it binds the UI and the API.

The real servers are never started; the fake `uv` logs each call, answers the health check
and lets the script's own inline Python run, so the test stays offline and takes a second.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEALTH = (
    '{"llm_configured": true, "domains": [{"label": "Cocktails", "enabled": true, '
    '"chat_enabled": true}]}'
)
FAKE_UV = f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$UV_LOG"
[[ "$1" == sync ]] && exit 0
shift
[[ "$1" == --no-sync ]] && shift
case "$1" in
    uvicorn) exec sleep 30 ;;
    streamlit) exit 0 ;;
    python)
        shift
        [[ "$1" == -m ]] && exit 0
        [[ "$2" == http* ]] && {{ cat >/dev/null; echo '{HEALTH}'; exit 0; }}
        exec "{sys.executable}" "$@" ;;
esac
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_quickstart(tmp_path: Path, **env: str) -> tuple[list[str], str]:
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text(FAKE_UV)
    fake.chmod(0o755)
    log = tmp_path / "uv.log"
    environ = {
        **{k: v for k, v in os.environ.items() if k not in ("UI_HOST", "UI_PORT", "API_PORT")},
        "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
        "UV_LOG": str(log),
        "CURIO_BOOKS": "0",
        **env,
    }
    done = subprocess.run(
        ["bash", str(ROOT / "scripts" / "quickstart.sh")],
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return log.read_text().splitlines(), done.stdout + done.stderr


def command(calls: list[str], program: str) -> list[str]:
    return next(call.split() for call in calls if f" {program} " in f" {call} ")


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def test_both_servers_listen_on_this_computer_only_by_default(tmp_path):
    calls, output = run_quickstart(tmp_path)
    assert option(command(calls, "streamlit"), "--server.address") == "127.0.0.1"
    assert option(command(calls, "uvicorn"), "--host") == "127.0.0.1"
    assert "open http://127.0.0.1:8501" in output
    assert "Warning" not in output


def test_the_ui_opens_to_the_network_only_on_request_and_says_so(tmp_path):
    calls, output = run_quickstart(tmp_path, UI_HOST="0.0.0.0", UI_PORT="18501")
    streamlit = command(calls, "streamlit")
    assert option(streamlit, "--server.address") == "0.0.0.0"
    assert option(streamlit, "--server.port") == "18501"
    assert option(command(calls, "uvicorn"), "--host") == "127.0.0.1"  # the API never moves
    assert "Warning: UI_HOST=0.0.0.0 serves the UI beyond this computer" in output
    assert "LLM key" in output
