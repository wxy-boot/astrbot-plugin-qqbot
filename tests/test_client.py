"""上游接口契约测试：请求头/请求体/重试策略/错误映射。"""

from __future__ import annotations

import pytest
from helpers import LOGIN_OK, FakeTransport, json_result, users_payload

from qbot import formatting as fmt
from qbot.client import (
    BotServiceClient,
    HttpResult,
    NetworkError,
    ServiceNotConfigured,
    Sub2ApiClient,
    UpstreamError,
    UpstreamTimeout,
    UserError,
)

SUB2API_BASE = "http://sub2api:8080/api/v1"


def make_sub2api(transport: FakeTransport, **overrides: object) -> Sub2ApiClient:
    params: dict[str, object] = {
        "base_url": SUB2API_BASE,
        "email": "bot@example.com",
        "password": "bot-password",
        "timeout": 10,
    }
    params.update(overrides)
    return Sub2ApiClient(transport, **params)  # type: ignore[arg-type]


def make_bot(transport: FakeTransport, **overrides: object) -> BotServiceClient:
    params: dict[str, object] = {
        "label": "签到服务",
        "base_url": "http://qiandao:8787/checkin",
        "secret": "s" * 32,
        "timeout": 10,
    }
    params.update(overrides)
    return BotServiceClient(transport, **params)  # type: ignore[arg-type]


# ---------------------------------------------------------------- Sub2API 登录


async def test_login_uses_post_body_and_caches_token() -> None:
    transport = FakeTransport([json_result(LOGIN_OK)])
    client = make_sub2api(transport)

    token = await client._ensure_token()
    assert token == "token-1"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == f"{SUB2API_BASE}/auth/login"
    assert call.json_body == {"email": "bot@example.com", "password": "bot-password"}
    assert call.timeout == 10.0

    # 第二次直接复用缓存，不再登录
    assert await client._ensure_token() == "token-1"
    assert len(transport.calls) == 1


async def test_login_missing_credentials() -> None:
    client = make_sub2api(FakeTransport([]), email="", password="")
    with pytest.raises(UserError, match="管理员凭据未配置"):
        await client._ensure_token()


async def test_login_423_compliance() -> None:
    transport = FakeTransport(
        [HttpResult(status=423, data={"code": 423, "message": "compliance"}, text="")]
    )
    client = make_sub2api(transport)
    with pytest.raises(UserError, match="合规确认"):
        await client._ensure_token()


async def test_login_401_means_bad_credentials() -> None:
    transport = FakeTransport(
        [HttpResult(status=401, data={"message": "invalid credentials"}, text="")]
    )
    client = make_sub2api(transport)
    with pytest.raises(UserError, match="管理员凭据配置错误"):
        await client._ensure_token()


async def test_relogin_after_401_on_get() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            HttpResult(status=401, data={"message": "token expired"}, text=""),
            json_result(LOGIN_OK, status=200),
            json_result(users_payload([{"id": 42, "email": "alice@example.com"}])),
        ]
    )
    client = make_sub2api(transport)
    user = await client.find_user_by_email("alice@example.com")
    assert user is not None and user["id"] == 42
    assert len(transport.calls) == 4
    assert transport.calls[2].url.endswith("/auth/login")


# ---------------------------------------------------------------- 用户检索行为


async def test_find_user_exact_match_only() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(
                users_payload(
                    [
                        {"id": 1, "email": "alice2@example.com"},
                        {"id": 2, "email": "alice@example.com"},
                    ]
                )
            ),
        ]
    )
    client = make_sub2api(transport)
    user = await client.find_user_by_email("Alice@Example.com ")
    assert user is not None and user["id"] == 2

    search_call = transport.calls[1]
    assert search_call.method == "GET"
    assert search_call.url == f"{SUB2API_BASE}/admin/users"
    assert search_call.params == {"page": 1, "page_size": 100, "search": "alice@example.com"}
    assert search_call.headers["Authorization"] == "Bearer token-1"


async def test_find_user_not_found_returns_none() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([{"id": 1, "email": "other@example.com"}])),
        ]
    )
    client = make_sub2api(transport)
    assert await client.find_user_by_email("alice@example.com") is None


async def test_find_user_paginates() -> None:
    first_page = [{"id": index, "email": f"u{index}@example.com"} for index in range(100)]
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload(first_page, total=150)),
            json_result(users_payload([{"id": 999, "email": "alice@example.com"}], total=150)),
        ]
    )
    client = make_sub2api(transport)
    user = await client.find_user_by_email("alice@example.com")
    assert user is not None and user["id"] == 999
    assert transport.calls[2].params["page"] == 2


async def test_find_user_too_many_pages() -> None:
    pages = [
        json_result(
            users_payload(
                [{"id": index, "email": f"u{index}@example.com"} for index in range(100)],
                total=5000,
            )
        )
        for _ in range(20)
    ]
    transport = FakeTransport([json_result(LOGIN_OK), *pages])
    client = make_sub2api(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.find_user_by_email("alice@example.com")
    assert excinfo.value.code == "too_many_results"


async def test_get_user_returns_data_object() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result({"code": 0, "data": {"id": "42", "balance": 5.2}}),
        ]
    )
    client = make_sub2api(transport)
    user = await client.get_user("42")
    assert user == {"id": "42", "balance": 5.2}
    assert transport.calls[1].url == f"{SUB2API_BASE}/admin/users/42"


async def test_get_user_404_maps_to_user_gone() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            HttpResult(status=404, data={"error": "not found"}, text=""),
        ]
    )
    client = make_sub2api(transport)
    with pytest.raises(UserError, match="不存在"):
        await client.get_user("42")


async def test_read_request_retries_once_on_network_error() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            NetworkError("boom"),
            json_result({"code": 0, "data": {"id": "42", "balance": 1}}),
        ]
    )
    client = make_sub2api(transport)
    user = await client.get_user("42")
    assert user["balance"] == 1
    assert len(transport.calls) == 3


async def test_read_request_timeout_retries_once_then_raises() -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), UpstreamTimeout(), UpstreamTimeout()]
    )
    client = make_sub2api(transport)
    with pytest.raises(UpstreamTimeout):
        await client.get_user("42")
    assert len(transport.calls) == 3


async def test_business_error_code_is_raised() -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result({"code": 1001, "message": "内部错误"}),
        ]
    )
    client = make_sub2api(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.get_user("42")
    assert excinfo.value.code == "1001"


# ------------------------------------------------------- 签到/抽奖机器人接口


async def test_checkin_request_shape() -> None:
    payload = {"ok": True, "alreadyCheckedIn": False, "entry": {"amount": 0.3}}
    transport = FakeTransport([json_result(payload)])
    client = make_bot(transport)

    result = await client.checkin("42", "alice")
    assert result["ok"] is True
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "http://qiandao:8787/checkin/api/bot/checkin"
    assert call.headers["X-Bot-Secret"] == "s" * 32
    assert call.json_body == {"userId": "42", "userName": "alice"}


async def test_status_request_shape() -> None:
    transport = FakeTransport([json_result({"ok": True, "checkedIn": False})])
    client = make_bot(transport)
    await client.status("42")
    call = transport.calls[0]
    assert call.method == "GET"
    assert call.url == "http://qiandao:8787/checkin/api/bot/status"
    assert call.params == {"userId": "42"}


async def test_write_request_is_not_retried() -> None:
    transport = FakeTransport([NetworkError("boom"), json_result({"ok": True})])
    client = make_bot(transport)
    with pytest.raises(NetworkError):
        await client.checkin("42", "alice")
    assert len(transport.calls) == 1


async def test_read_status_retries_once() -> None:
    transport = FakeTransport(
        [NetworkError("boom"), json_result({"ok": True, "checkedIn": True})]
    )
    client = make_bot(transport)
    result = await client.status("42")
    assert result["checkedIn"] is True
    assert len(transport.calls) == 2


async def test_not_configured_raises() -> None:
    client = make_bot(FakeTransport([]), secret="")
    with pytest.raises(ServiceNotConfigured):
        await client.checkin("42", "alice")


async def test_daily_spend_not_enough_carries_eligibility() -> None:
    payload = {
        "ok": False,
        "error": "daily_spend_not_enough",
        "message": "今日消费未达到签到门槛",
        "eligibility": {"enabled": True, "requiredAmount": 10, "currentAmount": 3.2, "remainingAmount": 6.8, "eligible": False},
    }
    transport = FakeTransport([json_result(payload, status=403)])
    client = make_bot(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.checkin("42", "alice")
    error = excinfo.value
    assert error.status == 403
    assert error.code == "daily_spend_not_enough"
    assert error.payload["eligibility"]["remainingAmount"] == 6.8


async def test_invalid_bot_secret_is_reported() -> None:
    transport = FakeTransport(
        [json_result({"ok": False, "error": "invalid_bot_secret", "message": "密钥错误"}, status=401)]
    )
    client = make_bot(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.status("42")
    assert excinfo.value.status == 401
    assert excinfo.value.code == "invalid_bot_secret"


async def test_ok_false_payload_without_http_error_is_raised() -> None:
    transport = FakeTransport(
        [json_result({"ok": False, "error": "internal_error", "message": "服务内部错误"}, status=200)]
    )
    client = make_bot(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.draw("42", "alice")
    assert excinfo.value.code == "internal_error"


async def test_unparsable_response_is_raised() -> None:
    transport = FakeTransport([HttpResult(status=200, data=None, text="<html>oops</html>")])
    client = make_bot(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.status("42")
    assert excinfo.value.code == "invalid_response"


async def test_504_maps_to_timeout_message() -> None:
    transport = FakeTransport([json_result({"ok": False, "error": "upstream_timeout"}, status=504)])
    client = make_bot(transport)
    with pytest.raises(UpstreamError) as excinfo:
        await client.status("42")
    assert str(excinfo.value) == fmt.MSG_TIMEOUT_UPSTREAM
