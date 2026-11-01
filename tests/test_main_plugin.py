"""AstrBot 适配层测试：命令注册、平台/群/自身过滤、冷却与异常兜底。

未安装 AstrBot 时整个模块跳过（插件运行环境一定安装了）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("astrbot")

from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402
from helpers import BASE_CONFIG  # noqa: E402

import main  # noqa: E402
from qbot import formatting as fmt  # noqa: E402
from qbot.app import BotService  # noqa: E402
from qbot.client import UpstreamError, UserError  # noqa: E402
from qbot.state import Binding, BindingStore  # noqa: E402

QQ = "123456789"
SELF_ID = "999"


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeEvent:
    """只实现插件用到的事件接口。"""

    def __init__(
        self,
        text: str,
        *,
        sender: str = QQ,
        group: str = "",
        platform: str = "aiocqhttp",
        self_id: str = SELF_ID,
    ) -> None:
        self.text = text
        self.sender = sender
        self.group = group
        self.platform = platform
        self.self_id = self_id
        self.stopped = False
        self.results: list[FakeResult] = []

    def get_platform_name(self) -> str:
        return self.platform

    def get_message_str(self) -> str:
        return self.text

    def get_sender_id(self) -> str:
        return self.sender

    def get_self_id(self) -> str:
        return self.self_id

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


def make_plugin(tmp_path: Path, **overrides: Any) -> main.Sub2ApiQqBotPlugin:
    config = {**BASE_CONFIG, **overrides}
    plugin = main.Sub2ApiQqBotPlugin(context=object(), config=config)
    store = BindingStore(tmp_path / "state.json")
    plugin.store = store
    plugin.service = BotService(
        plugin.config,
        store,
        plugin.sub2api,
        plugin.qiandao,
        plugin.choujiang,
    )
    return plugin


async def collect(agen: Any) -> list[Any]:
    return [item async for item in agen]


# ---------------------------------------------------------------- 命令注册


def test_handlers_are_registered_with_aliases() -> None:
    handlers = star_handlers_registry.get_handlers_by_module_name(main.__name__)
    found: dict[str, set[str]] = {}
    for handler in handlers:
        for event_filter in handler.event_filters:
            if isinstance(event_filter, CommandFilter):
                found[event_filter.command_name] = set(event_filter.alias)

    assert set(found) == {"绑定", "签到", "抽奖", "查询", "解绑", "帮助"}
    assert found["绑定"] == {"bind"}
    assert found["签到"] == {"checkin", "打卡"}
    assert found["抽奖"] == {"draw", "转盘"}
    assert found["查询"] == {"status", "我的"}
    assert found["解绑"] == {"unbind"}
    assert found["帮助"] == {"help"}


def test_plugin_metadata() -> None:
    assert main.PLUGIN_NAME == "astrbot_plugin_sub2api_qqbot"
    assert main.PLUGIN_VERSION == "v0.1.0"


# ---------------------------------------------------------------- /帮助


async def test_help_replies_and_stops(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    event = FakeEvent("帮助")

    results = await collect(plugin.cmd_help(event))
    assert len(results) == 1
    assert results[0].text == fmt.MSG_HELP
    assert event.stopped is True


async def test_help_works_even_when_state_is_corrupt(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("帮助")
    results = await collect(plugin.cmd_help(event))
    assert results[0].text == fmt.MSG_HELP


# ---------------------------------------------------------------- 过滤逻辑


async def test_other_platform_is_ignored(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    event = FakeEvent("帮助", platform="telegram")

    results = await collect(plugin.cmd_help(event))
    assert results == []
    assert event.stopped is False


async def test_self_message_is_ignored(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    event = FakeEvent("帮助", sender=SELF_ID)

    results = await collect(plugin.cmd_help(event))
    assert results == []


async def test_group_whitelist_filters_groups(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path, allowed_group_ids="100,200")

    blocked = FakeEvent("帮助", group="300")
    assert await collect(plugin.cmd_help(blocked)) == []
    assert blocked.stopped is False

    allowed = FakeEvent("帮助", group="100")
    assert await collect(plugin.cmd_help(allowed)) != []

    private = FakeEvent("帮助")
    assert await collect(plugin.cmd_help(private)) != []


async def test_empty_whitelist_allows_every_group(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path, allowed_group_ids="")
    event = FakeEvent("帮助", group="300")
    assert await collect(plugin.cmd_help(event)) != []


async def test_missing_self_id_does_not_break_commands(tmp_path: Path) -> None:
    """旧版 AstrBot 若没有 get_self_id，只跳过自身过滤，命令仍可用。"""

    class NoSelfIdEvent(FakeEvent):
        get_self_id = None  # type: ignore[assignment]

    plugin = make_plugin(tmp_path)
    event = NoSelfIdEvent("帮助")
    results = await collect(plugin.cmd_help(event))
    assert results[0].text == fmt.MSG_HELP


# ---------------------------------------------------------------- 冷却


async def test_cooldown_blocks_second_command(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path, command_cooldown_seconds=5)
    await plugin.store.load()

    first = FakeEvent("查询")
    first_results = await collect(plugin.cmd_status(first))
    assert first_results[0].text == fmt.MSG_UNBOUND
    assert first.stopped is True

    second = FakeEvent("查询")
    second_results = await collect(plugin.cmd_status(second))
    assert second_results[0].text.startswith("操作太频繁")
    assert second.stopped is True


async def test_help_is_exempt_from_cooldown(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path, command_cooldown_seconds=5)
    await plugin.store.load()

    for _ in range(3):
        event = FakeEvent("帮助")
        results = await collect(plugin.cmd_help(event))
        assert results[0].text == fmt.MSG_HELP


# ---------------------------------------------------------------- 命令分发


async def test_bind_without_argument_shows_usage(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("绑定")
    results = await collect(plugin.cmd_bind(event))
    assert results[0].text == fmt.MSG_USAGE_BIND


async def test_bind_argument_is_extracted(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("绑定 not-an-email")
    results = await collect(plugin.cmd_bind(event))
    assert results[0].text == fmt.MSG_INVALID_EMAIL


async def test_alias_message_is_parsed(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("bind not-an-email")
    results = await collect(plugin.cmd_bind(event))
    assert results[0].text == fmt.MSG_INVALID_EMAIL


async def test_unbound_checkin_prompts_bind(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("签到")
    results = await collect(plugin.cmd_checkin(event))
    assert results[0].text == fmt.MSG_UNBOUND
    assert event.stopped is True


async def test_status_reports_missing_binding(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("查询")
    results = await collect(plugin.cmd_status(event))
    assert results[0].text == fmt.MSG_UNBOUND


async def test_unbind_without_binding(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    event = FakeEvent("解绑")
    results = await collect(plugin.cmd_unbind(event))
    assert results[0].text == fmt.MSG_NOT_BOUND


# ---------------------------------------------------------------- 异常兜底


async def test_user_error_is_shown_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    async def boom(qq: str) -> str:
        raise UserError("自定义提示")

    monkeypatch.setattr(plugin.service, "checkin", boom)
    event = FakeEvent("签到")
    results = await collect(plugin.cmd_checkin(event))
    assert results[0].text == "自定义提示"


async def test_upstream_error_is_masked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    async def boom(qq: str) -> str:
        raise UpstreamError("内部细节", status=500, code="internal_error")

    monkeypatch.setattr(plugin.service, "draw", boom)
    event = FakeEvent("抽奖")
    results = await collect(plugin.cmd_draw(event))
    assert results[0].text == fmt.MSG_UPSTREAM_ERROR


async def test_unexpected_error_is_masked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = make_plugin(tmp_path)
    await plugin.store.load()

    async def boom(qq: str, *, in_group: bool = False) -> str:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(plugin.service, "status", boom)
    event = FakeEvent("查询")
    results = await collect(plugin.cmd_status(event))
    assert results[0].text == fmt.MSG_INTERNAL_ERROR
    assert event.stopped is True


async def test_initialize_loads_bindings_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plugin = make_plugin(tmp_path)
    store = BindingStore(tmp_path / "state.json")
    await store.load()
    await store.bind(QQ, Binding(uid="42", email="alice@example.com"))
    plugin.store = store
    plugin.service = BotService(
        plugin.config,
        store,
        plugin.sub2api,
        plugin.qiandao,
        plugin.choujiang,
    )

    await plugin.initialize()
    assert isinstance(plugin.store, BindingStore)
    assert await plugin.store.get(QQ) is not None
    await plugin.terminate()
