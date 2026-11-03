"""端到端集成测试：真实 HTTP 传输 + 模拟上游服务 + 真实命令处理器。

覆盖文档 14.3 的集成场景：
首次签到成功 / 二次已签到、首次抽奖 / 二次已抽、
门槛未开启放行、门槛开启且消费不足、网页端与 QQ 端每日状态互斥。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("astrbot")

from aiohttp import web  # noqa: E402
from helpers import BASE_CONFIG  # noqa: E402

import main  # noqa: E402
from qbot import formatting as fmt  # noqa: E402
from qbot.app import BotService  # noqa: E402
from qbot.state import BindingStore  # noqa: E402

QQ = "123456789"
UID = "42"
SECRET = BASE_CONFIG["bot_shared_secret"]


class MockBackend:
    """模拟 Sub2API + 签到 + 抽奖三个服务。"""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {
            UID: {
                "id": 42,
                "email": "alice@example.com",
                "username": "alice",
                "role": "user",
                "status": "active",
                "balance": 5.2,
            }
        }
        self.checkin_entries: dict[str, dict[str, Any]] = {}
        self.draw_entries: dict[str, dict[str, Any]] = {}
        self.checkin_eligibility: dict[str, Any] = {
            "enabled": False,
            "requiredAmount": 0,
            "currentAmount": None,
            "remainingAmount": 0,
            "unit": "USD",
            "timezone": "Asia/Shanghai",
            "eligible": True,
        }
        self.draw_eligibility: dict[str, Any] = {
            "enabled": False,
            "requiredAmount": 0,
            "currentAmount": None,
            "remainingAmount": 0,
            "unit": "USD",
            "timezone": "Asia/Shanghai",
            "eligible": True,
        }
        self.requests: list[str] = []
        # 领测试额度（挂在签到服务上）
        self.trial_config: dict[str, Any] = {
            "enabled": True,
            "amount": 1,
            "allowedDays": 3,
        }
        self.trial_window_expired = False
        self.trial_entries: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ Sub2API

    async def login(self, request: web.Request) -> web.Response:
        self.requests.append("login")
        body = await request.json()
        if body.get("email") != "bot@example.com" or body.get("password") != "bot-password":
            return web.json_response({"code": 401, "message": "bad"}, status=401)
        return web.json_response(
            {"code": 0, "data": {"access_token": "token-1", "expires_in": 86400}}
        )

    async def list_users(self, request: web.Request) -> web.Response:
        self.requests.append("list_users")
        if request.headers.get("Authorization") != "Bearer token-1":
            return web.json_response({"code": 401}, status=401)
        search = (request.query.get("search") or "").lower()
        items = [
            user
            for user in self.users.values()
            if search and search in str(user["email"]).lower()
        ]
        return web.json_response(
            {"code": 0, "data": {"items": items, "total": len(items)}}
        )

    async def get_user(self, request: web.Request) -> web.Response:
        self.requests.append("get_user")
        user = self.users.get(request.match_info["uid"])
        if user is None:
            return web.json_response({"code": 404, "message": "not found"}, status=404)
        return web.json_response({"code": 0, "data": user})

    # ------------------------------------------------------------ 签到服务

    def _check_secret(self, request: web.Request) -> web.Response | None:
        if request.headers.get("X-Bot-Secret") != SECRET:
            return web.json_response(
                {"ok": False, "error": "invalid_bot_secret", "message": "密钥错误"},
                status=401,
            )
        return None

    async def checkin(self, request: web.Request) -> web.Response:
        self.requests.append("checkin")
        error = self._check_secret(request)
        if error is not None:
            return error
        body = await request.json()
        uid = str(body.get("userId"))
        if uid not in self.users:
            return web.json_response(
                {"ok": False, "error": "user_not_found", "message": "用户不存在"},
                status=404,
            )
        eligibility = dict(self.checkin_eligibility)
        if eligibility.get("enabled") and not eligibility.get("eligible"):
            return web.json_response(
                {
                    "ok": False,
                    "error": "daily_spend_not_enough",
                    "message": "今日消费未达到签到门槛",
                    "eligibility": eligibility,
                },
                status=403,
            )
        entry = self.checkin_entries.get(uid)
        if entry is not None:
            return web.json_response(
                {
                    "ok": True,
                    "date": "2026-11-01",
                    "unit": "USD",
                    "alreadyCheckedIn": True,
                    "entry": entry,
                    "eligibility": eligibility,
                }
            )
        entry = {
            "date": "2026-11-01",
            "amount": 0.3,
            "unit": "USD",
            "rewardLabel": "连续签到奖励",
            "note": "",
            "createdAt": "2026-11-01T00:12:00.000Z",
        }
        self.checkin_entries[uid] = entry
        self.users[uid]["balance"] = round(float(self.users[uid]["balance"]) + 0.3, 2)
        return web.json_response(
            {
                "ok": True,
                "date": "2026-11-01",
                "unit": "USD",
                "alreadyCheckedIn": False,
                "entry": entry,
                "eligibility": eligibility,
            }
        )

    async def checkin_status(self, request: web.Request) -> web.Response:
        self.requests.append("checkin_status")
        error = self._check_secret(request)
        if error is not None:
            return error
        uid = str(request.query.get("userId"))
        entry = self.checkin_entries.get(uid)
        return web.json_response(
            {
                "ok": True,
                "date": "2026-11-01",
                "unit": "USD",
                "checkedIn": entry is not None,
                "entry": entry,
                "eligibility": self.checkin_eligibility,
            }
        )

    # ------------------------------------------------------------ 测试额度

    async def trial_credit(self, request: web.Request) -> web.Response:
        self.requests.append("trial_credit")
        error = self._check_secret(request)
        if error is not None:
            return error
        body = await request.json()
        uid = str(body.get("userId"))
        if uid not in self.users:
            return web.json_response(
                {"ok": False, "error": "user_not_found", "message": "用户不存在"},
                status=404,
            )
        trial = self.trial_config
        eligibility = {
            "enabled": trial["enabled"],
            "eligible": False,
            "amount": trial["amount"],
            "unit": "USD",
            "allowedDays": trial["allowedDays"],
            "registeredDays": 1.0,
            "reason": "",
            "message": "",
        }
        if not trial["enabled"]:
            return web.json_response(
                {
                    "ok": False,
                    "error": "trial_credit_disabled",
                    "message": "测试额度活动未开启。",
                    "eligibility": {**eligibility, "reason": "trial_credit_disabled"},
                },
                status=403,
            )
        entry = self.trial_entries.get(uid)
        if entry is not None:
            return web.json_response(
                {
                    "ok": True,
                    "alreadyClaimed": True,
                    "unit": "USD",
                    "entry": entry,
                    "eligibility": {**eligibility, "claimed": True, "reason": "already_claimed"},
                    "balance": self.users[uid]["balance"],
                }
            )
        if self.trial_window_expired:
            return web.json_response(
                {
                    "ok": False,
                    "error": "trial_window_expired",
                    "message": "账号注册超过 3 天，无法领取测试额度。",
                    "eligibility": {
                        **eligibility,
                        "registeredDays": 10.2,
                        "reason": "registration_window_expired",
                    },
                },
                status=403,
            )
        entry = {
            "date": "2026-11-01",
            "amount": trial["amount"],
            "unit": "USD",
            "rewardLabel": "测试额度",
            "note": "Trial credit 2026-11-01",
            "createdAt": "2026-11-03T02:00:00.000Z",
        }
        self.trial_entries[uid] = entry
        self.users[uid]["balance"] = round(
            float(self.users[uid]["balance"]) + float(trial["amount"]), 2
        )
        return web.json_response(
            {
                "ok": True,
                "alreadyClaimed": False,
                "unit": "USD",
                "entry": entry,
                "eligibility": {**eligibility, "eligible": True, "claimed": True},
                "balance": self.users[uid]["balance"],
            }
        )

    # ------------------------------------------------------------ 抽奖服务

    async def draw(self, request: web.Request) -> web.Response:
        self.requests.append("draw")
        error = self._check_secret(request)
        if error is not None:
            return error
        body = await request.json()
        uid = str(body.get("userId"))
        eligibility = dict(self.draw_eligibility)
        if eligibility.get("enabled") and not eligibility.get("eligible"):
            return web.json_response(
                {
                    "ok": False,
                    "error": "daily_spend_not_enough",
                    "message": "今日消费未达到抽奖门槛",
                    "eligibility": eligibility,
                },
                status=403,
            )
        entry = self.draw_entries.get(uid)
        if entry is not None:
            return web.json_response(
                {
                    "ok": True,
                    "date": "2026-11-01",
                    "unit": "USD",
                    "alreadyDrawn": True,
                    "prize": {"id": "p1", "label": "小锦鲤", "amount": 0.8},
                    "draw": entry,
                    "quota": {"limit": 1, "used": 1, "remaining": 0},
                    "eligibility": eligibility,
                }
            )
        entry = {
            "date": "2026-11-01",
            "prizeId": "p1",
            "prizeLabel": "小锦鲤",
            "amount": 0.8,
            "unit": "USD",
            "spendAmount": 12.34,
            "createdAt": "2026-11-01T02:00:00.000Z",
        }
        self.draw_entries[uid] = entry
        self.users[uid]["balance"] = round(float(self.users[uid]["balance"]) + 0.8, 2)
        return web.json_response(
            {
                "ok": True,
                "date": "2026-11-01",
                "unit": "USD",
                "alreadyDrawn": False,
                "prize": {"id": "p1", "label": "小锦鲤", "amount": 0.8},
                "prizeIndex": 3,
                "draw": entry,
                "quota": {"limit": 1, "used": 1, "remaining": 0},
                "eligibility": eligibility,
            }
        )

    async def draw_status(self, request: web.Request) -> web.Response:
        self.requests.append("draw_status")
        error = self._check_secret(request)
        if error is not None:
            return error
        uid = str(request.query.get("userId"))
        entry = self.draw_entries.get(uid)
        return web.json_response(
            {
                "ok": True,
                "date": "2026-11-01",
                "unit": "USD",
                "drawn": entry is not None,
                "draw": entry,
                "prize": {"id": "p1", "label": "小锦鲤", "amount": 0.8} if entry else None,
                "quota": {
                    "limit": 1,
                    "used": 1 if entry else 0,
                    "remaining": 0 if entry else 1,
                    "resetDate": "2026-11-02",
                    "resetAt": "2026-11-02T00:00:00.000+08:00",
                },
                "eligibility": self.draw_eligibility,
            }
        )


@pytest.fixture
async def backend() -> Any:
    backend = MockBackend()
    app = web.Application()
    app.router.add_post("/api/v1/auth/login", backend.login)
    app.router.add_get("/api/v1/admin/users", backend.list_users)
    app.router.add_get("/api/v1/admin/users/{uid}", backend.get_user)
    app.router.add_post("/checkin/api/bot/checkin", backend.checkin)
    app.router.add_get("/checkin/api/bot/status", backend.checkin_status)
    app.router.add_post("/checkin/api/bot/trial-credit", backend.trial_credit)
    app.router.add_post("/choujiang/api/bot/draw", backend.draw)
    app.router.add_get("/choujiang/api/bot/status", backend.draw_status)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    backend.base_url = f"http://127.0.0.1:{port}"
    try:
        yield backend
    finally:
        await runner.cleanup()


def make_plugin(tmp_path: Path, base_url: str) -> main.Sub2ApiQqBotPlugin:
    config = {
        **BASE_CONFIG,
        "sub2api_base_url": f"{base_url}/api/v1",
        "qiandao_base_url": f"{base_url}/checkin",
        "choujiang_base_url": f"{base_url}/choujiang",
    }
    plugin = main.Sub2ApiQqBotPlugin(context=object(), config=config)
    store = BindingStore(tmp_path / "state.json")
    plugin.store = store
    plugin.service = BotService(
        plugin.config,
        store,
        plugin.sub2api,
        plugin.qiandao,
        plugin.choujiang,
        plugin.trial,
    )
    return plugin


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeEvent:
    def __init__(self, text: str, *, group: str = "100") -> None:
        self.text = text
        self.group = group
        self.stopped = False
        self.results: list[FakeResult] = []

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_message_str(self) -> str:
        return self.text

    def get_sender_id(self) -> str:
        return QQ

    def get_self_id(self) -> str:
        return "999"

    def get_group_id(self) -> str:
        return self.group

    def is_private_chat(self) -> bool:
        return not self.group

    def plain_result(self, text: str) -> FakeResult:
        result = FakeResult(text)
        self.results.append(result)
        return result

    def stop_event(self) -> None:
        self.stopped = True


async def run(handler: Any, command: str, *, group: str = "100") -> str:
    event = FakeEvent(command, group=group)
    results = [item async for item in handler(event)]
    assert event.stopped is True
    assert len(results) == 1
    return results[0].text


async def test_full_flow_bind_checkin_draw_status_unbind(
    backend: MockBackend, tmp_path: Path
) -> None:
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()

    reply = await run(plugin.cmd_bind, "绑定 alice@example.com")
    assert reply.startswith("✅ 绑定成功")
    assert "账号：a***e@example.com" in reply
    assert "余额：5.20 USD" in reply

    reply = await run(plugin.cmd_checkin, "签到")
    assert reply == "✅ 签到成功，+0.30 USD（连续签到奖励）"

    reply = await run(plugin.cmd_checkin, "签到")
    assert reply == "今天已经签到过了，明天再来~"

    reply = await run(plugin.cmd_draw, "抽奖")
    assert reply == "🎉 恭喜抽中 0.80 USD（小锦鲤），已到账！"

    reply = await run(plugin.cmd_draw, "抽奖")
    assert reply == "今天已经抽过了：0.80 USD（小锦鲤）"

    reply = await run(plugin.cmd_status, "查询")
    lines = reply.splitlines()
    assert lines[0] == "📋 今日状态"
    assert lines[1] == "账号：a***e@example.com"
    assert lines[2] == "余额：6.30 USD"  # 5.20 + 0.30 + 0.80
    assert lines[3] == "签到：已签到 +0.30 USD（08:12）"
    assert lines[4] == "抽奖：已抽 0.80 USD（小锦鲤）"
    assert lines[5] == "签到门槛：未开启（不限消费）"
    assert lines[6] == "抽奖门槛：未开启（不限消费）"
    assert lines[7] == "抽奖机会：已用完"

    reply = await run(plugin.cmd_unbind, "解绑")
    assert reply.startswith("✅ 已解绑 a***e@example.com")

    reply = await run(plugin.cmd_checkin, "签到")
    assert reply.startswith("你还没有绑定账号")

    await plugin.terminate()


async def test_qq_sees_web_checkin(backend: MockBackend, tmp_path: Path) -> None:
    """网页端已签到，QQ 端不能再签（共用同一份服务端数据）。"""
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    # 模拟网页端已签到
    backend.checkin_entries[UID] = {
        "date": "2026-11-01",
        "amount": 0.3,
        "unit": "USD",
        "rewardLabel": "连续签到奖励",
        "createdAt": "2026-11-01T00:30:00.000Z",
    }

    reply = await run(plugin.cmd_checkin, "签到")
    assert reply == "今天已经签到过了，明天再来~"
    await plugin.terminate()


async def test_checkin_threshold_enabled_and_not_enough(
    backend: MockBackend, tmp_path: Path
) -> None:
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    backend.checkin_eligibility = {
        "enabled": True,
        "requiredAmount": 10,
        "currentAmount": 3.2,
        "remainingAmount": 6.8,
        "unit": "USD",
        "timezone": "Asia/Shanghai",
        "eligible": False,
    }
    reply = await run(plugin.cmd_checkin, "签到")
    assert reply == "今日消费 3.20/10.00 USD，未达到签到门槛（还差 6.80 USD）"

    # 消费达标后可以签到
    backend.checkin_eligibility = {
        "enabled": True,
        "requiredAmount": 10,
        "currentAmount": 12.34,
        "remainingAmount": 0,
        "unit": "USD",
        "timezone": "Asia/Shanghai",
        "eligible": True,
    }
    reply = await run(plugin.cmd_checkin, "签到")
    assert reply == "✅ 签到成功，+0.30 USD（连续签到奖励）"
    await plugin.terminate()


async def test_thresholds_are_independent(backend: MockBackend, tmp_path: Path) -> None:
    """签到门槛开启但抽奖门槛未开启：抽奖仍可进行。"""
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    backend.checkin_eligibility = {
        "enabled": True,
        "requiredAmount": 50,
        "currentAmount": 3.2,
        "remainingAmount": 46.8,
        "unit": "USD",
        "timezone": "Asia/Shanghai",
        "eligible": False,
    }

    reply = await run(plugin.cmd_draw, "抽奖")
    assert reply == "🎉 恭喜抽中 0.80 USD（小锦鲤），已到账！"

    reply = await run(plugin.cmd_checkin, "签到")
    assert "未达到签到门槛" in reply

    reply = await run(plugin.cmd_status, "查询")
    assert "签到门槛：50.00 USD（今日消费 3.20，还差 46.80）" in reply
    assert "抽奖门槛：未开启（不限消费）" in reply
    await plugin.terminate()


async def test_bad_secret_is_reported(backend: MockBackend, tmp_path: Path) -> None:
    config = {
        **BASE_CONFIG,
        "sub2api_base_url": f"{backend.base_url}/api/v1",
        "qiandao_base_url": f"{backend.base_url}/checkin",
        "choujiang_base_url": f"{backend.base_url}/choujiang",
        "bot_shared_secret": "wrong-secret-0123456789",
    }
    plugin = main.Sub2ApiQqBotPlugin(context=object(), config=config)
    store = BindingStore(tmp_path / "state.json")
    plugin.store = store
    plugin.service = BotService(
        plugin.config, store, plugin.sub2api, plugin.qiandao, plugin.choujiang
    )
    await store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    reply = await run(plugin.cmd_checkin, "签到")
    assert "密钥错误" in reply
    await plugin.terminate()


# ---------------------------------------------------------------- /领测试额度


async def test_trial_credit_flow(backend: MockBackend, tmp_path: Path) -> None:
    """首次领取 +1 USD，第二次同一账号不再发放，余额只加一次。"""
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    reply = await run(plugin.cmd_trial_credit, "领测试额度")
    assert reply == "✅ 领取成功，+1.00 USD 测试额度已到账\n当前余额：6.20 USD"
    assert backend.users[UID]["balance"] == 6.2

    reply = await run(plugin.cmd_trial_credit, "领测试额度")
    assert reply == fmt.MSG_TRIAL_ALREADY
    assert backend.users[UID]["balance"] == 6.2, "重复领取不能再加余额"

    reply = await run(plugin.cmd_status, "查询")
    assert "余额：6.20 USD" in reply
    await plugin.terminate()


async def test_trial_credit_rejects_expired_account(
    backend: MockBackend, tmp_path: Path
) -> None:
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    backend.trial_window_expired = True
    reply = await run(plugin.cmd_trial_credit, "领测试额度")

    assert "超过可领取范围" in reply
    assert backend.users[UID]["balance"] == 5.2, "超期不能发放余额"
    await plugin.terminate()


async def test_trial_credit_disabled_by_backend(
    backend: MockBackend, tmp_path: Path
) -> None:
    plugin = make_plugin(tmp_path, backend.base_url)
    await plugin.store.load()
    await run(plugin.cmd_bind, "绑定 alice@example.com")

    backend.trial_config["enabled"] = False
    reply = await run(plugin.cmd_trial_credit, "领测试额度")
    assert reply == fmt.MSG_TRIAL_DISABLED
    await plugin.terminate()
