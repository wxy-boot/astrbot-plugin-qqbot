"""测试用的假传输层与构造函数。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from qbot.app import BotService
from qbot.client import BotServiceClient, HttpResult, Sub2ApiClient
from qbot.config import PluginConfig
from qbot.state import BindingStore

BASE_CONFIG: dict[str, Any] = {
    "sub2api_base_url": "http://sub2api:8080/api/v1",
    "sub2api_admin_email": "bot@example.com",
    "sub2api_admin_password": "bot-password",
    "qiandao_base_url": "http://qiandao:8787/checkin",
    "choujiang_base_url": "http://choujiang:8788/choujiang",
    "bot_shared_secret": "0123456789abcdef0123456789",
    "request_timeout_seconds": 10,
    "command_cooldown_seconds": 0,
}

LOGIN_OK = {
    "code": 0,
    "message": "success",
    "data": {"access_token": "token-1", "expires_in": 86400},
}


class FakeTransport:
    """按顺序返回预置响应，并记录每次请求。"""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses: list[Any] = list(responses or [])
        self.calls: list[SimpleNamespace] = []
        self.closed = False

    def queue(self, *items: Any) -> "FakeTransport":
        self._responses.extend(items)
        return self

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> HttpResult:
        self.calls.append(
            SimpleNamespace(
                method=method,
                url=url,
                headers=dict(headers or {}),
                params=dict(params or {}),
                json_body=json_body,
                timeout=timeout,
            )
        )
        if not self._responses:
            raise AssertionError(f"没有预置响应，却收到请求 {method} {url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def json_result(payload: Any, status: int = 200) -> HttpResult:
    return HttpResult(
        status=status,
        data=payload,
        text=json.dumps(payload, ensure_ascii=False),
    )


class RoutingTransport:
    """按 ``"METHOD url"`` 子串路由的假传输，适合并发请求（``/查询``）。"""

    def __init__(self, routes: list[tuple[str, Any]]) -> None:
        self._routes = list(routes)
        self.calls: list[SimpleNamespace] = []
        self.closed = False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> HttpResult:
        self.calls.append(
            SimpleNamespace(
                method=method,
                url=url,
                headers=dict(headers or {}),
                params=dict(params or {}),
                json_body=json_body,
                timeout=timeout,
            )
        )
        target = f"{method} {url}"
        for matcher, response in self._routes:
            if matcher in target:
                if isinstance(response, Exception):
                    raise response
                if callable(response):
                    return response()
                return response
        raise AssertionError(f"未匹配到路由：{target}")

    async def close(self) -> None:
        self.closed = True



def users_payload(items: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "items": items,
            "total": len(items) if total is None else total,
        },
    }


def make_config(overrides: dict[str, Any] | None = None) -> PluginConfig:
    return PluginConfig.from_mapping({**BASE_CONFIG, **(overrides or {})})


def build_service(
    tmp_path: Any,
    transport: FakeTransport,
    overrides: dict[str, Any] | None = None,
) -> tuple[BotService, BindingStore, PluginConfig]:
    config = make_config(overrides)
    store = BindingStore(tmp_path / "state.json")
    sub2api = Sub2ApiClient(
        transport,
        base_url=config.sub2api_base_url,
        email=config.sub2api_admin_email,
        password=config.sub2api_admin_password,
        timeout=config.request_timeout,
    )
    qiandao = BotServiceClient(
        transport,
        label="签到服务",
        base_url=config.qiandao_base_url,
        secret=config.qiandao_secret,
        timeout=config.request_timeout,
    )
    choujiang = BotServiceClient(
        transport,
        label="抽奖服务",
        base_url=config.choujiang_base_url,
        secret=config.choujiang_secret,
        timeout=config.request_timeout,
    )
    return BotService(config, store, sub2api, qiandao, choujiang), store, config


def user_payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "id": 42,
        "email": "alice@example.com",
        "username": "alice",
        "role": "user",
        "status": "active",
        "balance": 5.2,
    }
    data.update(overrides)
    return data
