"""Web app network exposure: bind address, Host check, Socket.IO origin check."""

import asyncio
from unittest.mock import MagicMock

import pytest

from nxbt.web.app import DEFAULT_IP, allowed_hosts

LOCAL = "127.0.0.1:8000"


def get(app, path, headers, query=b""):
    """Sends one GET straight to the ASGI app and returns the status code."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


@pytest.fixture
def app(web_app):
    return web_app.app_for(DEFAULT_IP)


@pytest.fixture
def web_app():
    from nxbt.web.app import WebApp

    return WebApp(nxbt=MagicMock())


def test_defaults_to_this_machine_only():
    assert DEFAULT_IP == "127.0.0.1"


def test_allowed_hosts():
    assert "localhost" in allowed_hosts("127.0.0.1")
    assert "192.168.1.20" in allowed_hosts("192.168.1.20")
    assert allowed_hosts("0.0.0.0") == ["*"]


def test_page_served_for_local_host(app):
    assert get(app, "/", {"Host": LOCAL}) == 200


def test_foreign_host_rejected(app):
    # DNS rebinding: a malicious domain resolving to 127.0.0.1
    assert get(app, "/", {"Host": "evil.example:8000"}) == 400


def socketio_handshake(app, origin):
    return get(
        app,
        "/socket.io/",
        {"Host": LOCAL, "Origin": origin},
        query=b"EIO=4&transport=polling",
    )


def test_socketio_accepts_same_origin(app):
    assert socketio_handshake(app, f"http://{LOCAL}") == 200


def test_socketio_rejects_other_origins(app):
    assert socketio_handshake(app, "http://evil.example") == 400
