"""GitHub Actions workflows: pinned actions, least privilege, and the release order."""

import re
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
