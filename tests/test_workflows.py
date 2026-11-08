"""GitHub Actions workflows: pinned actions, least privilege, and the release order."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_by_commit_sha_with_its_version(path):
    uses = re.findall(r"uses:\s*(\S+)(.*)", path.read_text())
    assert uses
    for action, comment in uses:
        name, _, ref = action.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{name} is pinned to {ref!r}, not a commit"
        assert re.fullmatch(r"\s*# v\d+\.\d+\.\d+", comment), f"{name} lacks '# vX.Y.Z'"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_default_token_permissions_and_each_job_declares_its_own(path):
    workflow = load(path)
    assert workflow["permissions"] == {}
    for name, job in workflow["jobs"].items():
        assert "permissions" in job, f"{path.name}: job {name} has no permissions"
        assert job.get("timeout-minutes"), f"{path.name}: job {name} has no timeout"
        writes = {scope for scope, level in job["permissions"].items() if level == "write"}
        assert writes <= {"contents", "packages"}, (name, writes)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkouts_do_not_keep_the_token(path):
    for job in load(path)["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_the_release_is_published_only_after_the_image_is_pushed():
    jobs = load(ROOT / ".github" / "workflows" / "release.yml")["jobs"]
    assert jobs["image"]["needs"] == "plan"
    assert set(jobs["publish"]["needs"]) == {"plan", "image"}
    # Only the publishing job may write the repository (tags and releases).
    writers = {n for n, j in jobs.items() if j["permissions"].get("contents") == "write"}
    assert writers == {"publish"}
    assert {n for n, j in jobs.items() if j["permissions"].get("packages") == "write"} == {"image"}
    image = "\n".join(step.get("run", "") for step in jobs["image"]["steps"])
    publish = "\n".join(step.get("run", "") for step in jobs["publish"]["steps"])
    assert "docker push" in image and "gh release create" not in image
    assert "gh release create" in publish
    # Before it creates the release, the publishing job checks that the image is on ghcr.io.
    assert publish.index("docker manifest inspect") < publish.index("gh release create")
    # Secrets reach scripts through env, never interpolated into the script text.
    for job in jobs.values():
        for step in job["steps"]:
            assert "${{ github.token }}" not in step.get("run", "")


def test_ci_runs_the_browser_suite_on_the_demo_data():
    jobs = load(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    steps = jobs["e2e-demo"]["steps"]
    runs = [step.get("run", "") for step in steps]
    assert any("scripts.prepare_books --source .cache/goodbooks-10k" in r for r in runs)
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))
    assert cache["with"]["key"] == "goodbooks-10k-${{ steps.books.outputs.commit }}"
    suite = next(s for s in steps if s.get("run", "").startswith("uv run pytest -m e2e tests/e2e"))
    assert suite["env"]["E2E_REQUIRE_DATA"] == "1"


# --- The release scripts, run with stand-ins for git, gh and docker ---------------------------


def _release_step(job: str, name: str) -> dict:
    jobs = load(ROOT / ".github" / "workflows" / "release.yml")["jobs"]
    return next(step for step in jobs[job]["steps"] if step.get("name", "").startswith(name))


def _run_step(step: dict, tmp_path: Path, env: dict[str, str], tools: dict[str, str]):
    """Run a step's script as GitHub does (bash -eo pipefail) with fake commands on PATH."""
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in tools.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/bash\n{body}\n")
        path.chmod(0o755)
    output = tmp_path / "output"
    output.write_text("")
    full = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": "owner/curio",
        **env,
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=full,
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return result, outputs


def _plan(tmp_path: Path, release_list: str):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "3.1.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [3.1.0]\n\n- Things.\n")
    gh = f"""
case "$1 $2" in
  "release view") exit 1 ;;
  "release list") {release_list} ;;
esac"""
    step = _release_step("plan", "Read the version")
    return _run_step(step, tmp_path, {}, {"git": "echo abc123", "gh": gh})


def test_the_release_plan_fails_when_it_cannot_list_releases(tmp_path):
    # It used to treat a failed listing as "no releases" and move `latest` to this version.
    result, outputs = _plan(tmp_path, "echo 'HTTP 502' >&2; exit 1")
    assert result.returncode != 0
    assert "latest" not in outputs


@pytest.mark.parametrize(
    ("published", "latest"),
    [("echo v3.0.0", "true"), ("printf 'v3.0.0\\nv3.2.0\\n'", "false"), ("true", "true")],
)
def test_latest_moves_only_to_the_highest_release(tmp_path, published, latest):
    result, outputs = _plan(tmp_path, published)
    assert result.returncode == 0, result.stderr
    assert outputs["latest"] == latest and outputs["released"] == "false"
    assert outputs["sha"] == "abc123" and outputs["version"] == "3.1.0"


def _docker(revision_json: str, exists: bool = True) -> str:
    return f"""
if [ "$1 $2" = "manifest inspect" ]; then exit {0 if exists else 1}; fi
if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then echo '{revision_json}'; exit 0; fi
exit 0"""


needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq, as on the runners")
IMAGE_ENV = {"IMAGE": "ghcr.io/owner/curio", "VERSION": "3.1.0", "SHA": "abc123"}
LABELS = '"Labels":{"org.opencontainers.image.revision":"%s"}'


@pytest.mark.parametrize(
    "image",
    [
        '{"config":{%s}}' % (LABELS % "old999"),
        '{"linux/amd64":{"config":{%s}}}' % (LABELS % "old999"),
    ],
    ids=["manifest", "index"],
)
@needs_jq
def test_an_image_built_from_another_commit_stops_the_release(tmp_path, image):
    # The image exists but was built from another commit: tagging this one would pair the
    # release with an image of different code.
    step = _release_step("image", "Skip if this version is already published")
    result, outputs = _run_step(step, tmp_path, IMAGE_ENV, {"docker": _docker(image)})
    assert result.returncode != 0
    assert "old999" in result.stdout + result.stderr
    assert "done" not in outputs


@needs_jq
def test_an_image_of_this_commit_is_not_rebuilt(tmp_path):
    step = _release_step("image", "Skip if this version is already published")
    image = '{"config":{%s}}' % (LABELS % "abc123")
    result, outputs = _run_step(step, tmp_path, IMAGE_ENV, {"docker": _docker(image)})
    assert result.returncode == 0, result.stderr
    assert outputs["done"] == "true"
    missing = _run_step(step, tmp_path, IMAGE_ENV, {"docker": _docker("{}", exists=False)})
    assert missing[0].returncode == 0 and missing[1]["done"] == "false"


def test_latest_is_pushed_even_when_the_version_image_already_exists():
    # A run whose `latest` push failed is finished by the next one, which finds the version
    # image published and skips the build.
    steps = load(ROOT / ".github" / "workflows" / "release.yml")["jobs"]["image"]["steps"]
    latest = [s for s in steps if "latest" in s.get("run", "")]
    assert len(latest) == 1
    assert latest[0]["if"] == "env.LATEST == 'true'"
    assert "steps.published" not in latest[0]["if"]
    push = next(s for s in steps if s.get("name") == "Push to ghcr.io")
    assert "latest" not in push["run"]
