"""真实 HTTP 传输层契约测试（本地 aiohttp 服务）。"""

from __future__ import annotations

import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from qbot.client import (  # noqa: E402
    AiohttpTransport,
    NetworkError,
    UpstreamError,
    UpstreamTimeout,
)


async def _start(app: web.Application) -> tuple[str, web.AppRunner]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return f"http://127.0.0.1:{port}", runner


async def test_transport_serializes_request_and_response() -> None:
    captured: dict[str, object] = {}

    async def checkin(request: web.Request) -> web.Response:
        captured["method"] = request.method
        captured["path"] = request.path
        captured["query"] = dict(request.query)
        captured["secret"] = request.headers.get("X-Bot-Secret")
        captured["body"] = await request.json()
        return web.json_response({"ok": True, "entry": {"amount": 0.3}})

    app = web.Application()
    app.router.add_post("/checkin/api/bot/checkin", checkin)
    base_url, runner = await _start(app)
    transport = AiohttpTransport(total_timeout=5)
    try:
        result = await transport.request(
            "POST",
            f"{base_url}/checkin/api/bot/checkin",
            headers={"X-Bot-Secret": "secret-value"},
            json_body={"userId": "42", "userName": "alice"},
        )
    finally:
        await transport.close()
        await runner.cleanup()

    assert result.status == 200
    assert result.data == {"ok": True, "entry": {"amount": 0.3}}
    assert captured["method"] == "POST"
    assert captured["path"] == "/checkin/api/bot/checkin"
    assert captured["secret"] == "secret-value"
    assert captured["body"] == {"userId": "42", "userName": "alice"}


async def test_transport_passes_query_params() -> None:
    captured: dict[str, object] = {}

    async def status(request: web.Request) -> web.Response:
        captured["query"] = dict(request.query)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/checkin/api/bot/status", status)
    base_url, runner = await _start(app)
    transport = AiohttpTransport(total_timeout=5)
    try:
        await transport.request(
            "GET",
            f"{base_url}/checkin/api/bot/status",
            params={"userId": "42"},
        )
    finally:
        await transport.close()
        await runner.cleanup()

    assert captured["query"] == {"userId": "42"}


async def test_transport_does_not_raise_on_http_error_status() -> None:
    async def forbidden(request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": False, "error": "daily_spend_not_enough"}, status=403
        )

    app = web.Application()
    app.router.add_post("/checkin/api/bot/checkin", forbidden)
    base_url, runner = await _start(app)
    transport = AiohttpTransport(total_timeout=5)
    try:
        result = await transport.request(
            "POST", f"{base_url}/checkin/api/bot/checkin", json_body={"userId": "42"}
        )
    finally:
        await transport.close()
        await runner.cleanup()

    assert result.status == 403
    assert result.data is not None
    assert result.data["error"] == "daily_spend_not_enough"


async def test_transport_timeout_raises_upstream_timeout() -> None:
    async def slow(request: web.Request) -> web.Response:
        await asyncio.sleep(1.0)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/slow", slow)
    base_url, runner = await _start(app)
    transport = AiohttpTransport(total_timeout=0.2)
    try:
        with pytest.raises(UpstreamTimeout):
            await transport.request("GET", f"{base_url}/slow")
    finally:
        await transport.close()
        await runner.cleanup()


async def test_transport_connection_error_raises_network_error() -> None:
    async def hang_up(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # 接受连接后立即关闭，触发 aiohttp 的 ServerDisconnectedError（ClientError）
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transport = AiohttpTransport(total_timeout=2)
    try:
        with pytest.raises(NetworkError):
            await transport.request("GET", f"http://127.0.0.1:{port}/nothing")
    finally:
        await transport.close()
        server.close()
        await server.wait_closed()


async def test_transport_close_is_idempotent() -> None:
    transport = AiohttpTransport(total_timeout=1)
    await transport.close()
    await transport.close()


async def test_non_json_body_is_reported_as_none() -> None:
    async def html(request: web.Request) -> web.Response:
        return web.Response(text="<html>not json</html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/html", html)
    base_url, runner = await _start(app)
    transport = AiohttpTransport(total_timeout=5)
    try:
        result = await transport.request("GET", f"{base_url}/html")
    finally:
        await transport.close()
        await runner.cleanup()

    assert result.data is None
    assert "not json" in result.text
    assert isinstance(result.status, int)


def test_upstream_error_defaults() -> None:
    error = UpstreamError("boom")
    assert error.status is None
    assert error.code is None
    assert error.payload == {}

