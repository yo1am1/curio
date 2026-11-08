"""Guards for the eval tests: the runner configures the process like a CLI, so a test that
calls it must restore the environment, or later tests (tests/app) read its values."""

import os

import pytest

WATCHED = (
    "AGENT_TURN_DEADLINE_SECONDS",
    "LLM_TIMEOUT_SECONDS",
    "AGENT_SIGNING_KEY",
    "ENABLED_DOMAINS",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
)


@pytest.fixture(autouse=True)
def no_environment_leak():
    before = {name: os.environ.get(name) for name in WATCHED}
    yield
    after = {name: os.environ.get(name) for name in WATCHED}
    leaked = {name for name in WATCHED if before[name] != after[name]}
    assert not leaked, f"the test left these environment variables changed: {sorted(leaked)}"
