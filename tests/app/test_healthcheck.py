"""The container health check (`python -m curio.app.healthcheck`) against a real server."""

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from curio.app.api import create_app
from curio.app.config import Settings
from curio.app.healthcheck import check, host_header
from tests.conftest import serve


@pytest.fixture
def api_port() -> Iterator[int]:
    """The API with no domains, answering only Host `curio.lan`, on a free local port."""
    app = create_app(serve(), settings=Settings(_env_file=None, allowed_hosts="curio.lan"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.02)
        yield sock.getsockname()[1]
        server.should_exit = True
        thread.join(10)


def test_host_header_is_the_first_plain_allowed_name():
    assert host_header(None) == "localhost"
    assert host_header("") == "localhost"
    assert host_header("*.example.com, Curio.LAN,localhost") == "curio.lan"
    assert host_header("*") == "localhost"


def test_healthy_when_the_api_answers_under_an_allowed_name(api_port):
    assert check(api_port, "curio.lan")
    # The DNS-rebinding guard answers 400 to a name it does not allow: not healthy.
    assert not check(api_port, "localhost")


def test_unhealthy_when_nothing_listens():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert not check(port, None)
