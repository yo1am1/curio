"""`scripts/quickstart.sh` with a recording `uv` and `npm`: where it binds the one server, when
it builds the web app, and what it prepares.

The script runs from a copy of the repository layout in a temporary folder (the script and a
miniature `web/`), so building or not building never touches the real `web/dist`. The real
server is never started; the fake `uv` logs each call, answers the health check and lets the
script's own inline Python run, so the test stays offline and takes a second.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
    uvicorn) exec sleep 1 ;;
    python)
        shift
        [[ "$1" == -m ]] && exit 0
        [[ "$2" == http* ]] && {{ cat >/dev/null; echo '{HEALTH}'; exit 0; }}
        exec "{sys.executable}" "$@" ;;
esac
"""
FAKE_NPM = """#!/usr/bin/env bash
printf 'npm %s (in %s)\\n' "$*" "$(basename "$PWD")" >> "$UV_LOG"
if [[ "$1" == run && "$2" == build ]]; then
    mkdir -p dist && echo '<div id="root">' > dist/index.html
fi
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def checkout(tmp_path: Path, *, built: bool = True) -> Path:
    """The script and a miniature web app in `tmp_path/repo`; `built` adds a current build."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "quickstart.sh", repo / "scripts" / "quickstart.sh")
    (repo / "web" / "src").mkdir(parents=True)
    for name in ("package.json", "package-lock.json", "index.html", "vite.config.ts"):
        (repo / "web" / name).write_text("{}")
    (repo / "web" / "src" / "main.tsx").write_text("")
    if built:
        (repo / "web" / "dist").mkdir()
        index = repo / "web" / "dist" / "index.html"
        index.write_text('<div id="root">')
        later = index.stat().st_mtime + 60
        os.utime(index, (later, later))
    return repo


def run_quickstart(
    tmp_path: Path,
    *,
    repo: Path | None = None,
    npm: bool = True,
    status: int = 0,
    **env: str | None,
) -> tuple[list[str], str]:
    """Run the script with `env` on top of a test environment; None unsets a variable."""
    repo = repo or checkout(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, script in (("uv", FAKE_UV), ("npm", FAKE_NPM)):
        fake = bin_dir / name
        if name == "npm" and not npm:
            continue
        fake.write_text(script)
        fake.chmod(0o755)
    log = tmp_path / "uv.log"
    log.touch()
    # Only the fake tools and the basics: a real npm on PATH must never run.
    tools = ("bash", "find", "seq", "sleep", "dirname", "basename", "mkdir", "cat")
    basics = {Path(shutil.which(tool)).parent for tool in tools}
    environ = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("CURIO_", "UI_", "API_"))},
        "PATH": os.pathsep.join([str(bin_dir), *map(str, basics)]),
        "UV_LOG": str(log),
        "CURIO_BOOKS": "0",
        "DATA_DIR": str(tmp_path / "data"),
        **env,
    }
    environ = {k: v for k, v in environ.items() if v is not None}
    done = subprocess.run(
        ["bash", str(repo / "scripts" / "quickstart.sh")],
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = done.stdout + done.stderr
    assert done.returncode == status, output
    return log.read_text().splitlines(), output


def command(calls: list[str], program: str) -> list[str]:
    return next(call.split() for call in calls if f" {program} " in f" {call} ")


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def test_one_server_listens_on_this_computer_only_by_default(tmp_path):
    calls, output = run_quickstart(tmp_path)
    uvicorn = command(calls, "uvicorn")
    assert option(uvicorn, "--host") == "127.0.0.1"
    assert option(uvicorn, "--port") == "8000"
    assert "open http://127.0.0.1:8000" in output
    assert not any("streamlit" in call for call in calls)
    assert "Warning" not in output


def test_the_server_opens_to_the_network_only_on_request_and_says_so(tmp_path):
    calls, output = run_quickstart(tmp_path, CURIO_HOST="0.0.0.0", CURIO_PORT="18000")
    uvicorn = command(calls, "uvicorn")
    assert option(uvicorn, "--host") == "0.0.0.0"
    assert option(uvicorn, "--port") == "18000"
    assert "open http://127.0.0.1:18000" in output
    assert "Warning: CURIO_HOST=0.0.0.0 serves Curio beyond this computer" in output


def test_opening_the_network_names_the_hosts_the_api_answers(tmp_path):
    _, output = run_quickstart(tmp_path, CURIO_HOST="0.0.0.0", ALLOWED_HOSTS=None)
    assert "host names in ALLOWED_HOSTS (localhost,127.0.0.1)" in output
    (tmp_path / "ip").mkdir()
    _, output = run_quickstart(tmp_path / "ip", CURIO_HOST="192.168.1.20", ALLOWED_HOSTS=None)
    # The address the script prints (and health-checks) is allowed.
    assert "ALLOWED_HOSTS (localhost,127.0.0.1,192.168.1.20)" in output
    assert "LLM key" in output


def test_the_web_app_is_built_when_missing_or_stale_and_reused_when_current(tmp_path):
    (tmp_path / "missing").mkdir()
    repo = checkout(tmp_path / "missing", built=False)
    calls, output = run_quickstart(tmp_path / "missing", repo=repo)
    assert "npm ci --no-audit --no-fund --loglevel=error (in web)" in calls
    assert "npm run build --silent (in web)" in calls
    assert "Building the web app" in output
    assert calls.index("npm run build --silent (in web)") < calls.index(
        next(call for call in calls if "uvicorn" in call)
    )

    (tmp_path / "current").mkdir()
    calls, output = run_quickstart(tmp_path / "current")
    assert not any(call.startswith("npm") for call in calls)

    (tmp_path / "stale").mkdir()
    repo = checkout(tmp_path / "stale")
    source = repo / "web" / "src" / "main.tsx"
    later = (repo / "web" / "dist" / "index.html").stat().st_mtime + 60
    os.utime(source, (later, later))
    calls, _ = run_quickstart(tmp_path / "stale", repo=repo)
    assert "npm run build --silent (in web)" in calls

    (tmp_path / "off").mkdir()
    repo = checkout(tmp_path / "off", built=False)
    calls, _ = run_quickstart(tmp_path / "off", repo=repo, CURIO_WEB_BUILD="0")
    assert not any(call.startswith("npm") for call in calls)


def test_without_npm_a_missing_build_explains_what_to_do(tmp_path):
    repo = checkout(tmp_path, built=False)
    calls, output = run_quickstart(tmp_path, repo=repo, npm=False, status=1)
    assert "npm is not installed" in output
    assert "docker compose --profile demo up --build" in output
    assert not any("uvicorn" in call for call in calls)
    # A build made before is served as it is, even when its sources changed since.
    (tmp_path / "built").mkdir()
    repo = checkout(tmp_path / "built")
    later = (repo / "web" / "dist" / "index.html").stat().st_mtime + 60
    os.utime(repo / "web" / "src" / "main.tsx", (later, later))
    calls, output = run_quickstart(tmp_path / "built", repo=repo, npm=False)
    assert "serving the web app built before" in output
    assert any("uvicorn" in call for call in calls)


def test_books_are_prepared_by_default_with_an_opt_out(tmp_path):
    """The one-command demo includes books, so it has pairings and Plan my evening."""
    (tmp_path / "default").mkdir()
    calls, output = run_quickstart(tmp_path / "default", CURIO_BOOKS=None)
    assert any("-m scripts.prepare_cocktails" in call for call in calls)
    assert any("-m scripts.prepare_books" in call for call in calls)
    assert "Preparing the book catalog" in output
    (tmp_path / "off").mkdir()
    calls, _ = run_quickstart(tmp_path / "off", CURIO_BOOKS="0")
    assert any("-m scripts.prepare_cocktails" in call for call in calls)
    assert not any("-m scripts.prepare_books" in call for call in calls)


def test_the_docker_demo_prepares_books_by_default_with_an_opt_out():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "DEMO_BOOKS: ${DEMO_BOOKS:-1}" in compose
    assert 'if [ "$$DEMO_BOOKS" = 1 ]' in compose


def test_one_container_serves_the_api_and_the_app():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "streamlit" not in compose and "8501" not in compose
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY --from=web /web/dist ./web/dist" in dockerfile
    assert "EXPOSE 8000\n" in dockerfile


def _compose_services() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def test_compose_publishes_ports_on_loopback_only_unless_opted_in():
    services = _compose_services()
    published = {name: service["ports"] for name, service in services.items() if "ports" in service}
    assert set(published) == {"demo-api", "api"}
    for ports in published.values():
        # host:port:container with the host defaulting to 127.0.0.1; CURIO_HOST opts in.
        assert ports == ["${CURIO_HOST:-127.0.0.1}:${CURIO_PORT:-8000}:8000"]
    for name in published:
        environment = services[name]["environment"]
        assert environment["ALLOWED_HOSTS"] == "${ALLOWED_HOSTS:-localhost,127.0.0.1}"


def test_compose_never_publishes_qdrant():
    qdrant = _compose_services()["qdrant"]
    assert "ports" not in qdrant and "expose" not in qdrant


def test_every_base_image_is_pinned_by_version_and_digest():
    """`uv:0.11` floated across patch releases; a tag can also be re-pushed."""
    import re

    dockerfile = (ROOT / "Dockerfile").read_text()
    images = re.findall(r"^FROM (\S+)", dockerfile, re.M) + re.findall(
        r"COPY --from=(\S+:\S+)", dockerfile
    )
    assert len(images) == 3
    for image in images:
        name, _, digest = image.partition("@sha256:")
        assert re.fullmatch(r"[0-9a-f]{64}", digest), image
        assert re.search(r":\d+\.\d+\.\d+", name), image  # an exact version, not 3.13 or 0.11
    assert "ghcr.io/astral-sh/uv:0.11.32@" in dockerfile
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert 'version: "0.11.32"' in ci  # CI and the image use one uv
