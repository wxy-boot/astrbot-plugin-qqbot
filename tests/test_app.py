"""命令业务逻辑测试：绑定、签到、抽奖、查询、解绑与错误文案。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from helpers import (
    LOGIN_OK,
    FakeTransport,
    RoutingTransport,
    build_service,
    json_result,
    user_payload,
    users_payload,
)

from qbot import formatting as fmt
from qbot.app import extract_argument, is_valid_email, normalize_email
from qbot.client import NetworkError, UserError
from qbot.state import Binding

QQ = "123456789"


def binding(uid: str = "42", email: str = "alice@example.com") -> Binding:
    return Binding(uid=uid, email=email, name="alice", bound_at=1762000000)


async def prepared(tmp_path: Path, transport: Any, **overrides: Any):
    service, store, config = build_service(tmp_path, transport, overrides or None)
    await store.load()
    return service, store, config


# ---------------------------------------------------------------- 参数解析


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("绑定 alice@example.com", "alice@example.com"),
        ("bind alice@example.com", "alice@example.com"),
        ("BIND   alice@example.com  ", "alice@example.com"),
        ("绑定", ""),
        ("绑定   ", ""),
        ("帮助", ""),
    ],
)
def test_extract_argument(message: str, expected: str) -> None:
    assert extract_argument(message, ("绑定", "bind")) == expected


def test_extract_argument_keeps_extra_tokens() -> None:
    assert extract_argument("绑定 a@b.com c@d.com", ("绑定", "bind")) == "a@b.com c@d.com"


def test_email_helpers() -> None:
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
    assert is_valid_email("alice@example.com") is True
    assert is_valid_email("alice@example") is False
    assert is_valid_email("alice example.com") is False
    assert is_valid_email("") is False


# ---------------------------------------------------------------- /绑定


async def test_bind_usage_errors(tmp_path: Path) -> None:
    service, _, _ = await prepared(tmp_path, FakeTransport([]))
    with pytest.raises(UserError, match="用法"):
        await service.bind(QQ, "")
    with pytest.raises(UserError, match="用法"):
        await service.bind(QQ, "a@b.com c@d.com")


async def test_bind_invalid_email(tmp_path: Path) -> None:
    service, _, _ = await prepared(tmp_path, FakeTransport([]))
    with pytest.raises(UserError, match="邮箱格式不正确"):
        await service.bind(QQ, "not-an-email")


async def test_bind_requires_admin_credentials(tmp_path: Path) -> None:
    transport = FakeTransport([])
    service, _, _ = await prepared(
        tmp_path, transport, sub2api_admin_email="", sub2api_admin_password=""
    )
    with pytest.raises(UserError, match="管理员凭据未配置"):
        await service.bind(QQ, "alice@example.com")
    assert transport.calls == []


async def test_bind_success_masks_email_in_group(tmp_path: Path) -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user_payload()]))]
    )
    service, store, _ = await prepared(tmp_path, transport)

    reply = await service.bind(QQ, "Alice@Example.com", in_group=True)
    assert reply == (
        "✅ 绑定成功\n"
        "账号：a***e@example.com\n"
        "余额：5.20 USD\n"
        "可用命令：/签到 /抽奖 /查询 /解绑 /帮助"
    )
    stored = await store.get(QQ)
    assert stored is not None
    assert stored.uid == "42"
    assert stored.email == "alice@example.com"
    assert stored.name == "alice"
    assert stored.bound_at > 0


async def test_bind_shows_full_email_in_private(tmp_path: Path) -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user_payload()]))]
    )
    service, _, _ = await prepared(tmp_path, transport)
    reply = await service.bind(QQ, "alice@example.com", in_group=False)
    assert "账号：alice@example.com" in reply


async def test_bind_masks_disabled_shows_full_email_in_group(tmp_path: Path) -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user_payload()]))]
    )
    service, _, _ = await prepared(tmp_path, transport, mask_email_in_group=False)
    reply = await service.bind(QQ, "alice@example.com", in_group=True)
    assert "账号：alice@example.com" in reply


async def test_bind_same_email_twice(tmp_path: Path) -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user_payload()]))]
    )
    service, _, _ = await prepared(tmp_path, transport)
    await service.bind(QQ, "alice@example.com")
    with pytest.raises(UserError, match="已经绑定该账号"):
        await service.bind(QQ, "ALICE@example.com")


async def test_bind_other_email_requires_unbind(tmp_path: Path) -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user_payload()]))]
    )
    service, _, _ = await prepared(tmp_path, transport)
    await service.bind(QQ, "alice@example.com")
    with pytest.raises(UserError, match="请先 /解绑"):
        await service.bind(QQ, "bob@example.com")


async def test_bind_unknown_email(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([{"id": 7, "email": "other@example.com"}])),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport)
    with pytest.raises(UserError, match="未找到该邮箱"):
        await service.bind(QQ, "alice@example.com")


async def test_bind_rejects_inactive_account(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([user_payload(status="banned")])),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport)
    with pytest.raises(UserError, match="不是正常启用状态"):
        await service.bind(QQ, "alice@example.com")


async def test_bind_rejects_unknown_role(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([user_payload(role="guest")])),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport)
    with pytest.raises(UserError, match="角色不支持绑定"):
        await service.bind(QQ, "alice@example.com")


async def test_bind_rejects_admin_by_default(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([user_payload(role="admin")])),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport)
    with pytest.raises(UserError, match="管理员账号"):
        await service.bind(QQ, "alice@example.com")


async def test_bind_allows_admin_when_enabled(tmp_path: Path) -> None:
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([user_payload(role="admin")])),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport, allow_bind_admin=True)
    reply = await service.bind(QQ, "alice@example.com")
    assert reply.startswith("✅ 绑定成功")


async def test_bind_rejects_uid_bound_to_another_qq(tmp_path: Path) -> None:
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user_payload()]))]
    )
    service, store, _ = await prepared(tmp_path, transport)
    await store.bind("999", binding())

    with pytest.raises(UserError, match="已绑定其他 QQ"):
        await service.bind(QQ, "alice@example.com")


async def test_bind_fetches_balance_when_missing(tmp_path: Path) -> None:
    search_user = user_payload()
    search_user.pop("balance")
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([search_user])),
            json_result({"code": 0, "data": {"id": 42, "balance": 9.99}}),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport)
    reply = await service.bind(QQ, "alice@example.com")
    assert "余额：9.99 USD" in reply


async def test_bind_tolerates_balance_lookup_failure(tmp_path: Path) -> None:
    search_user = user_payload()
    search_user.pop("balance")
    transport = FakeTransport(
        [
            json_result(LOGIN_OK),
            json_result(users_payload([search_user])),
            NetworkError("boom"),
            NetworkError("boom"),
        ]
    )
    service, _, _ = await prepared(tmp_path, transport)
    reply = await service.bind(QQ, "alice@example.com")
    assert "余额：查询失败" in reply


async def test_bind_uses_email_prefix_when_no_username(tmp_path: Path) -> None:
    user = user_payload()
    user.pop("username")
    transport = FakeTransport(
        [json_result(LOGIN_OK), json_result(users_payload([user]))]
    )
    service, store, _ = await prepared(tmp_path, transport)
    await service.bind(QQ, "alice@example.com")
    stored = await store.get(QQ)
    assert stored is not None and stored.name == "alice"


# ---------------------------------------------------------------- /签到


async def test_checkin_unbound(tmp_path: Path) -> None:
    service, _, _ = await prepared(tmp_path, FakeTransport([]))
    with pytest.raises(UserError, match="/绑定"):
        await service.checkin(QQ)


async def test_checkin_missing_secret(tmp_path: Path) -> None:
    service, store, _ = await prepared(
        tmp_path, FakeTransport([]), bot_shared_secret=""
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError, match="未配置机器人密钥"):
        await service.checkin(QQ)


async def test_checkin_success(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "unit": "USD",
        "alreadyCheckedIn": False,
        "entry": {"amount": 0.3, "unit": "USD", "rewardLabel": "连续签到奖励"},
        "eligibility": {"enabled": True, "requiredAmount": 10, "currentAmount": 12.34, "remainingAmount": 0, "eligible": True},
    }
    transport = FakeTransport([json_result(payload)])
    service, store, _ = await prepared(tmp_path, transport)
    await store.bind(QQ, binding())

    reply = await service.checkin(QQ)
    assert reply == "✅ 签到成功，+0.30 USD（连续签到奖励）"
    call = transport.calls[0]
    assert call.json_body == {"userId": "42", "userName": "alice"}


async def test_checkin_already(tmp_path: Path) -> None:
    payload = {"ok": True, "alreadyCheckedIn": True, "entry": {"amount": 0.3}}
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    assert await service.checkin(QQ) == fmt.MSG_CHECKIN_ALREADY


async def test_checkin_threshold_not_enough(tmp_path: Path) -> None:
    payload = {
        "ok": False,
        "error": "daily_spend_not_enough",
        "message": "今日消费未达到签到门槛",
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": 3.2,
            "remainingAmount": 6.8,
            "unit": "USD",
            "eligible": False,
        },
    }
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=403)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.checkin(QQ)
    assert str(excinfo.value) == "今日消费 3.20/10.00 USD，未达到签到门槛（还差 6.80 USD）"


async def test_checkin_threshold_check_failed(tmp_path: Path) -> None:
    payload = {
        "ok": False,
        "error": "daily_spend_not_enough",
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": None,
            "remainingAmount": 0,
            "eligible": True,
            "error": "missing_daily_spend_endpoint",
        },
    }
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=403)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.checkin(QQ)
    assert str(excinfo.value) == fmt.MSG_CHECKIN_THRESHOLD_FAILED


async def test_checkin_threshold_disabled_still_succeeds(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "unit": "USD",
        "alreadyCheckedIn": False,
        "entry": {"amount": 0.1},
        "eligibility": {
            "enabled": False,
            "requiredAmount": 0,
            "currentAmount": None,
            "remainingAmount": 0,
            "eligible": True,
        },
    }
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    reply = await service.checkin(QQ)
    assert reply == "✅ 签到成功，+0.10 USD"
    assert "门槛" not in reply


async def test_checkin_never_reports_success_without_entry(tmp_path: Path) -> None:
    """服务返回 200 但没有签到记录且门槛未达标：不能放行。"""
    payload = {
        "ok": True,
        "alreadyCheckedIn": False,
        "entry": None,
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": 3.2,
            "remainingAmount": 6.8,
            "unit": "USD",
            "eligible": False,
        },
    }
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    with pytest.raises(UserError, match="未达到签到门槛"):
        await service.checkin(QQ)


async def test_checkin_never_reports_success_when_check_failed(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "alreadyCheckedIn": False,
        "eligibility": {"enabled": True, "requiredAmount": 10, "currentAmount": None},
    }
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.checkin(QQ)
    assert str(excinfo.value) == fmt.MSG_CHECKIN_THRESHOLD_FAILED


async def test_checkin_invalid_secret(tmp_path: Path) -> None:
    payload = {"ok": False, "error": "invalid_bot_secret", "message": "密钥错误"}
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=401)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError, match="签到服务未配置或密钥错误"):
        await service.checkin(QQ)


async def test_checkin_network_failure(tmp_path: Path) -> None:
    service, store, _ = await prepared(tmp_path, FakeTransport([NetworkError("boom")]))
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.checkin(QQ)
    assert str(excinfo.value) == fmt.MSG_CHECKIN_UNAVAILABLE


async def test_checkin_user_not_found(tmp_path: Path) -> None:
    payload = {"ok": False, "error": "user_not_found", "message": "用户不存在"}
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=404)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError, match="不存在，请重新绑定"):
        await service.checkin(QQ)


async def test_checkin_passes_through_service_message(tmp_path: Path) -> None:
    payload = {"ok": False, "error": "already_full", "message": "奖池已抽空，请明天再来"}
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=400)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.checkin(QQ)
    # HTTP 400 映射为通用提示，不泄露原始错误
    assert str(excinfo.value) == fmt.MSG_INVALID_USER_ID


# ---------------------------------------------------------------- /抽奖


async def test_draw_success(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "unit": "USD",
        "alreadyDrawn": False,
        "prize": {"id": "p1", "label": "小锦鲤", "amount": 0.8},
        "draw": {"amount": 0.8, "prizeLabel": "小锦鲤"},
        "quota": {"limit": 1, "used": 1, "remaining": 0},
        "eligibility": {"enabled": True, "requiredAmount": 10, "currentAmount": 12.34, "remainingAmount": 0, "eligible": True},
    }
    transport = FakeTransport([json_result(payload)])
    service, store, _ = await prepared(tmp_path, transport)
    await store.bind(QQ, binding())

    reply = await service.draw(QQ)
    assert reply == "🎉 恭喜抽中 0.80 USD（小锦鲤），已到账！"
    assert transport.calls[0].url == "http://choujiang:8788/choujiang/api/bot/draw"


async def test_draw_zero_amount_prize(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "unit": "USD",
        "alreadyDrawn": False,
        "prize": {"label": "谢谢参与", "amount": 0},
        "draw": {"amount": 0, "prizeLabel": "谢谢参与"},
    }
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    assert await service.draw(QQ) == "🎉 抽中「谢谢参与」，本次没有余额奖励"


async def test_draw_already(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "alreadyDrawn": True,
        "prize": {"label": "好运到", "amount": 0.3},
        "draw": {"amount": 0.3, "prizeLabel": "好运到"},
    }
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    assert await service.draw(QQ) == "今天已经抽过了：0.30 USD（好运到）"


async def test_draw_threshold_not_enough(tmp_path: Path) -> None:
    payload = {
        "ok": False,
        "error": "daily_spend_not_enough",
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": 3.2,
            "remainingAmount": 6.8,
            "unit": "USD",
            "eligible": False,
        },
    }
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=403)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.draw(QQ)
    assert str(excinfo.value) == "今日消费 3.20/10.00 USD，未获得抽奖机会（还差 6.80 USD）"


async def test_draw_threshold_check_failed(tmp_path: Path) -> None:
    payload = {
        "ok": False,
        "error": "daily_spend_not_enough",
        "eligibility": {"enabled": True, "requiredAmount": 10, "currentAmount": None, "eligible": True},
    }
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=403)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.draw(QQ)
    assert str(excinfo.value) == fmt.MSG_DRAW_THRESHOLD_FAILED


async def test_draw_never_reports_prize_without_draw_record(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "alreadyDrawn": False,
        "draw": None,
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": 3.2,
            "remainingAmount": 6.8,
            "eligible": False,
        },
    }
    service, store, _ = await prepared(tmp_path, FakeTransport([json_result(payload)]))
    await store.bind(QQ, binding())
    with pytest.raises(UserError, match="未获得抽奖机会"):
        await service.draw(QQ)


async def test_draw_bot_disabled(tmp_path: Path) -> None:
    payload = {"ok": False, "error": "bot_disabled", "message": "bot disabled"}
    service, store, _ = await prepared(
        tmp_path, FakeTransport([json_result(payload, status=403)])
    )
    await store.bind(QQ, binding())
    with pytest.raises(UserError, match="抽奖服务未配置或密钥错误"):
        await service.draw(QQ)


async def test_draw_network_failure(tmp_path: Path) -> None:
    service, store, _ = await prepared(tmp_path, FakeTransport([NetworkError("boom")]))
    await store.bind(QQ, binding())
    with pytest.raises(UserError) as excinfo:
        await service.draw(QQ)
    assert str(excinfo.value) == fmt.MSG_DRAW_UNAVAILABLE


# ---------------------------------------------------------------- /查询


async def test_status_unbound(tmp_path: Path) -> None:
    service, _, _ = await prepared(tmp_path, FakeTransport([]))
    with pytest.raises(UserError, match="/绑定"):
        await service.status(QQ)


def status_transport(
    *,
    checkin_payload: Any,
    draw_payload: Any,
    user: Any = None,
) -> RoutingTransport:
    routes: list[tuple[str, Any]] = [
        ("POST http://sub2api:8080/api/v1/auth/login", json_result(LOGIN_OK)),
        (
            "GET http://sub2api:8080/api/v1/admin/users/42",
            json_result({"code": 0, "data": user if user is not None else {"id": 42, "balance": 5.2}}),
        ),
    ]
    if isinstance(checkin_payload, Exception):
        routes.append(("http://qiandao:8787/checkin/api/bot/status", checkin_payload))
    else:
        routes.append(
            ("http://qiandao:8787/checkin/api/bot/status", json_result(checkin_payload))
        )
    if isinstance(draw_payload, Exception):
        routes.append(("http://choujiang:8788/choujiang/api/bot/status", draw_payload))
    else:
        routes.append(
            ("http://choujiang:8788/choujiang/api/bot/status", json_result(draw_payload))
        )
    return RoutingTransport(routes)


async def test_status_card_both_thresholds_enabled(tmp_path: Path) -> None:
    checkin_payload = {
        "ok": True,
        "unit": "USD",
        "checkedIn": True,
        "entry": {"amount": 0.3, "unit": "USD", "createdAt": "2026-11-01T00:12:00.000Z"},
        "eligibility": {
            "enabled": True,
            "requiredAmount": 5,
            "currentAmount": 12.34,
            "remainingAmount": 0,
            "unit": "USD",
            "eligible": True,
        },
    }
    draw_payload = {
        "ok": True,
        "drawn": True,
        "draw": {"amount": 0.8, "prizeLabel": "小锦鲤"},
        "prize": {"label": "小锦鲤", "amount": 0.8},
        "quota": {"limit": 1, "used": 1, "remaining": 0},
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": 12.34,
            "remainingAmount": 0,
            "unit": "USD",
            "eligible": True,
        },
    }
    service, store, _ = await prepared(
        tmp_path, status_transport(checkin_payload=checkin_payload, draw_payload=draw_payload)
    )
    await store.bind(QQ, binding())

    reply = await service.status(QQ, in_group=True)
    assert reply == "\n".join(
        [
            "📋 今日状态",
            "账号：a***e@example.com",
            "余额：5.20 USD",
            "签到：已签到 +0.30 USD（08:12）",
            "抽奖：已抽 0.80 USD（小锦鲤）",
            "签到门槛：5.00 USD（今日消费 12.34，已达标）",
            "抽奖门槛：10.00 USD（今日消费 12.34，已达标）",
            "抽奖机会：已用完",
        ]
    )


async def test_status_card_one_threshold_disabled(tmp_path: Path) -> None:
    checkin_payload = {
        "ok": True,
        "unit": "USD",
        "checkedIn": True,
        "entry": {"amount": 0.3, "unit": "USD", "createdAt": "2026-11-01T00:12:00.000Z"},
        "eligibility": {
            "enabled": False,
            "requiredAmount": 0,
            "currentAmount": None,
            "remainingAmount": 0,
            "eligible": True,
        },
    }
    draw_payload = {
        "ok": True,
        "drawn": False,
        "draw": None,
        "quota": {"limit": 1, "used": 0, "remaining": 1},
        "eligibility": {
            "enabled": True,
            "requiredAmount": 10,
            "currentAmount": 3.2,
            "remainingAmount": 6.8,
            "unit": "USD",
            "eligible": False,
        },
    }
    service, store, _ = await prepared(
        tmp_path, status_transport(checkin_payload=checkin_payload, draw_payload=draw_payload)
    )
    await store.bind(QQ, binding())

    reply = await service.status(QQ, in_group=True)
    assert reply == "\n".join(
        [
            "📋 今日状态",
            "账号：a***e@example.com",
            "余额：5.20 USD",
            "签到：已签到 +0.30 USD（08:12）",
            "抽奖：未抽奖",
            "签到门槛：未开启（不限消费）",
            "抽奖门槛：10.00 USD（今日消费 3.20，还差 6.80）",
            "抽奖机会：剩余 1 次",
        ]
    )


async def test_status_card_partial_failure(tmp_path: Path) -> None:
    checkin_payload: Any = NetworkError("boom")
    draw_payload = {
        "ok": True,
        "drawn": False,
        "quota": {"remaining": 1},
        "eligibility": {"enabled": False, "currentAmount": None, "eligible": True},
    }
    service, store, _ = await prepared(
        tmp_path, status_transport(checkin_payload=checkin_payload, draw_payload=draw_payload)
    )
    await store.bind(QQ, binding())

    reply = await service.status(QQ, in_group=True)
    lines = reply.splitlines()
    assert lines[3] == "签到：查询失败"
    assert lines[5] == "签到门槛：查询失败"
    assert lines[4] == "抽奖：未抽奖"
    assert lines[6] == "抽奖门槛：未开启（不限消费）"
    assert lines[7] == "抽奖机会：剩余 1 次"


async def test_status_card_balance_failure(tmp_path: Path) -> None:
    checkin_payload = {"ok": True, "checkedIn": False, "eligibility": {"enabled": False}}
    draw_payload = {
        "ok": True,
        "drawn": False,
        "quota": {"remaining": 0},
        "eligibility": {"enabled": False},
    }
    transport = status_transport(checkin_payload=checkin_payload, draw_payload=draw_payload)
    service, store, _ = await prepared(tmp_path, transport)
    await store.bind(QQ, binding())

    # 让用户查询返回 500
    transport._routes[1] = (
        "GET http://sub2api:8080/api/v1/admin/users/42",
        json_result({"code": 500, "message": "boom"}, status=500),
    )
    reply = await service.status(QQ, in_group=True)
    assert "余额：查询失败" in reply


async def test_status_unconfigured_services(tmp_path: Path) -> None:
    service, store, _ = await prepared(
        tmp_path,
        FakeTransport(
            [
                json_result(LOGIN_OK),
                json_result({"code": 0, "data": {"id": 42, "balance": 5.2}}),
            ]
        ),
        bot_shared_secret="",
    )
    await store.bind(QQ, binding())
    reply = await service.status(QQ, in_group=True)
    lines = reply.splitlines()
    assert lines[3] == "签到：未配置"
    assert lines[4] == "抽奖：未配置"
    assert lines[5] == "签到门槛：未配置"
    assert lines[6] == "抽奖门槛：未配置"
    assert lines[7] == "抽奖机会：未配置"


# ---------------------------------------------------------------- /解绑


async def test_unbind(tmp_path: Path) -> None:
    service, store, _ = await prepared(tmp_path, FakeTransport([]))
    await store.bind(QQ, binding())

    reply = await service.unbind(QQ, in_group=True)
    assert reply == (
        "✅ 已解绑 a***e@example.com\n"
        "绑定关系已删除，历史签到/抽奖记录仍在服务端。"
    )
    assert await store.get(QQ) is None


async def test_unbind_when_not_bound(tmp_path: Path) -> None:
    service, _, _ = await prepared(tmp_path, FakeTransport([]))
    with pytest.raises(UserError, match="你还没有绑定账号"):
        await service.unbind(QQ)


# ---------------------------------------------------------------- 只读保护


async def test_corrupt_state_blocks_commands(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    service, store, _ = await prepared(tmp_path, FakeTransport([]))
    assert store.read_only_reason == "corrupt"

    for action in (
        lambda: service.bind(QQ, "alice@example.com"),
        lambda: service.checkin(QQ),
        lambda: service.draw(QQ),
        lambda: service.status(QQ),
        lambda: service.unbind(QQ),
    ):
        with pytest.raises(UserError) as excinfo:
            await action()
        assert str(excinfo.value) == fmt.MSG_STATE_CORRUPT

    # /帮助 始终可用
    assert service.help_text() == fmt.MSG_HELP


async def test_newer_state_version_blocks_commands(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(
        '{"version": 99, "bindings": {}}', encoding="utf-8"
    )
    service, store, _ = await prepared(tmp_path, FakeTransport([]))
    assert store.read_only_reason == "version"
    with pytest.raises(UserError) as excinfo:
        await service.status(QQ)
    assert str(excinfo.value) == fmt.MSG_STATE_UNSUPPORTED
