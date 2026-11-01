"""Sub2API QQ 助手 —— AstrBot 插件入口。

命令：``/绑定`` ``/签到`` ``/抽奖`` ``/查询`` ``/解绑`` ``/帮助``。
业务逻辑在 ``qbot`` 包内（不依赖 AstrBot），本文件只做适配：
平台/群白名单过滤、命令冷却、异常兜底与消息回复。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:  # AstrBot 以 data.plugins.<dir>.main 的形式载入插件
    from .qbot import app as qbot_app
    from .qbot import client as qbot_client
    from .qbot import config as qbot_config
    from .qbot import formatting as fmt
    from .qbot import ratelimit as qbot_ratelimit
    from .qbot import state as qbot_state
except ImportError:  # pragma: no cover - 在插件目录内直接 import main 时
    import qbot.app as qbot_app
    import qbot.client as qbot_client
    import qbot.config as qbot_config
    import qbot.formatting as fmt
    import qbot.ratelimit as qbot_ratelimit
    import qbot.state as qbot_state

PLUGIN_NAME = "astrbot_plugin_sub2api_qqbot"
PLUGIN_AUTHOR = "wxy-boot"
PLUGIN_DESC = "Sub2API 账号绑定、每日签到、消费达标抽奖与状态查询。"
PLUGIN_VERSION = "v0.1.0"
PLUGIN_REPO = "https://github.com/wxy-boot/astrbot-plugin-qqbot"

PLATFORM_NAME = "aiocqhttp"

CMD_BIND = ("绑定", "bind")
CMD_CHECKIN = ("签到", "checkin", "打卡")
CMD_DRAW = ("抽奖", "draw", "转盘")
CMD_STATUS = ("查询", "status", "我的")
CMD_UNBIND = ("解绑", "unbind")


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION, PLUGIN_REPO)
class Sub2ApiQqBotPlugin(Star):
    """Sub2API QQ 助手：绑定账号、每日签到、消费达标抽奖、状态查询。"""

    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        # 配置不合法时抛出 ValueError，让 AstrBot 在日志中明确报错
        self.config = qbot_config.PluginConfig.from_mapping(config)
        state_path = Path(__file__).resolve().parent / qbot_state.STATE_FILENAME
        self.store = qbot_state.BindingStore(state_path)
        self.transport = qbot_client.AiohttpTransport(self.config.request_timeout)
        self.sub2api = qbot_client.Sub2ApiClient(
            self.transport,
            base_url=self.config.sub2api_base_url,
            email=self.config.sub2api_admin_email,
            password=self.config.sub2api_admin_password,
            timeout=self.config.request_timeout,
        )
        self.qiandao = qbot_client.BotServiceClient(
            self.transport,
            label="签到服务",
            base_url=self.config.qiandao_base_url,
            secret=self.config.qiandao_secret,
            timeout=self.config.request_timeout,
        )
        self.choujiang = qbot_client.BotServiceClient(
            self.transport,
            label="抽奖服务",
            base_url=self.config.choujiang_base_url,
            secret=self.config.choujiang_secret,
            timeout=self.config.request_timeout,
        )
        self.service = qbot_app.BotService(
            self.config,
            self.store,
            self.sub2api,
            self.qiandao,
            self.choujiang,
        )
        self.cooldown = qbot_ratelimit.CommandCooldown(
            self.config.command_cooldown_seconds
        )

    # ------------------------------------------------------------ 生命周期钩子

    async def initialize(self) -> None:
        """载入绑定关系并提示配置状态。"""
        await self.store.load()
        if self.store.read_only:
            logger.error(
                "Sub2API QQ 助手：绑定数据处于只读保护状态（%s），"
                "除 /帮助 外的命令都会提示联系管理员",
                self.store.read_only_reason,
            )
        else:
            logger.info("Sub2API QQ 助手已就绪，当前绑定 %s 个 QQ", len(self.store))
        if not self.config.has_admin_credentials:
            logger.warning(
                "Sub2API QQ 助手：未配置管理员凭据，/绑定 与 /查询 余额不可用"
            )
        if not self.config.qiandao_secret:
            logger.warning("Sub2API QQ 助手：未配置签到服务机器人密钥，/签到 不可用")
        if not self.config.choujiang_secret:
            logger.warning("Sub2API QQ 助手：未配置抽奖服务机器人密钥，/抽奖 不可用")

    async def terminate(self) -> None:
        """插件卸载/停用时关闭 HTTP 会话。"""
        await self.transport.close()

    # ---------------------------------------------------------------- 命令实现

    @filter.command("绑定", alias={"bind"})
    async def cmd_bind(self, event: AstrMessageEvent):
        """绑定 Sub2API 账号：/绑定 注册邮箱"""
        if not self._should_handle(event):
            return
        wait = self._cooldown_wait(event)
        if wait:
            yield event.plain_result(fmt.cooldown_text(wait))
            event.stop_event()
            return
        qq = self._sender_id(event)
        argument = qbot_app.extract_argument(self._message_text(event), CMD_BIND)
        in_group = self._in_group(event)
        reply = await self._run(
            lambda: self.service.bind(qq, argument, in_group=in_group)
        )
        yield event.plain_result(reply)
        event.stop_event()

    @filter.command("签到", alias={"checkin", "打卡"})
    async def cmd_checkin(self, event: AstrMessageEvent):
        """每日签到：/签到"""
        if not self._should_handle(event):
            return
        wait = self._cooldown_wait(event)
        if wait:
            yield event.plain_result(fmt.cooldown_text(wait))
            event.stop_event()
            return
        qq = self._sender_id(event)
        reply = await self._run(lambda: self.service.checkin(qq))
        yield event.plain_result(reply)
        event.stop_event()

    @filter.command("抽奖", alias={"draw", "转盘"})
    async def cmd_draw(self, event: AstrMessageEvent):
        """每日抽奖：/抽奖"""
        if not self._should_handle(event):
            return
        wait = self._cooldown_wait(event)
        if wait:
            yield event.plain_result(fmt.cooldown_text(wait))
            event.stop_event()
            return
        qq = self._sender_id(event)
        reply = await self._run(lambda: self.service.draw(qq))
        yield event.plain_result(reply)
        event.stop_event()

    @filter.command("查询", alias={"status", "我的"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看余额与今日状态：/查询"""
        if not self._should_handle(event):
            return
        wait = self._cooldown_wait(event)
        if wait:
            yield event.plain_result(fmt.cooldown_text(wait))
            event.stop_event()
            return
        qq = self._sender_id(event)
        in_group = self._in_group(event)
        reply = await self._run(lambda: self.service.status(qq, in_group=in_group))
        yield event.plain_result(reply)
        event.stop_event()

    @filter.command("解绑", alias={"unbind"})
    async def cmd_unbind(self, event: AstrMessageEvent):
        """解除当前 QQ 的绑定：/解绑"""
        if not self._should_handle(event):
            return
        wait = self._cooldown_wait(event)
        if wait:
            yield event.plain_result(fmt.cooldown_text(wait))
            event.stop_event()
            return
        qq = self._sender_id(event)
        in_group = self._in_group(event)
        reply = await self._run(lambda: self.service.unbind(qq, in_group=in_group))
        yield event.plain_result(reply)
        event.stop_event()

    @filter.command("帮助", alias={"help"})
    async def cmd_help(self, event: AstrMessageEvent):
        """显示命令说明：/帮助"""
        if not self._should_handle(event):
            return
        # /帮助 不受冷却限制，也不访问上游
        yield event.plain_result(self.service.help_text())
        event.stop_event()

    # ---------------------------------------------------------------- 过滤逻辑

    def _should_handle(self, event: AstrMessageEvent) -> bool:
        """平台过滤、忽略自己、群白名单。"""
        try:
            if event.get_platform_name() != PLATFORM_NAME:
                return False
        except Exception:
            logger.exception("Sub2API QQ 助手：读取平台名称失败")
            return False

        sender = self._sender_id(event)
        self_id = ""
        try:
            self_id = str(event.get_self_id() or "")
        except Exception:
            # 版本差异时只是跳过自身消息过滤，不影响命令可用性
            logger.debug("Sub2API QQ 助手：读取机器人自身 QQ 失败", exc_info=True)
        if sender and self_id and sender == self_id:
            return False
        return self._allowed_group(event)

    def _allowed_group(self, event: AstrMessageEvent) -> bool:
        whitelist = self.config.allowed_group_ids
        if not whitelist:
            return True
        try:
            raw = str(event.get_group_id() or "").strip()
        except Exception:
            return False
        if not raw:  # 私聊放行
            return True
        try:
            return int(raw) in whitelist
        except ValueError:
            logger.warning("Sub2API QQ 助手：无法解析群号 %r，按不在白名单处理", raw)
            return False

    def _cooldown_wait(self, event: AstrMessageEvent) -> int:
        return self.cooldown.check(self._sender_id(event))

    def _sender_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            logger.exception("Sub2API QQ 助手：读取发送者 QQ 失败")
            return ""

    def _in_group(self, event: AstrMessageEvent) -> bool:
        try:
            return not event.is_private_chat()
        except Exception:
            try:
                return bool(str(event.get_group_id() or "").strip())
            except Exception:
                return False

    @staticmethod
    def _message_text(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:
                logger.exception("Sub2API QQ 助手：读取消息文本失败")
        return str(getattr(event, "message_str", "") or "")

    # ---------------------------------------------------------------- 异常兜底

    async def _run(self, action: Callable[[], Awaitable[str]]) -> str:
        """执行命令逻辑，把异常统一转换成可回复的文案。"""
        try:
            return await action()
        except qbot_client.UserError as exc:
            return str(exc)
        except qbot_client.BotError as exc:
            logger.warning("Sub2API QQ 助手：上游异常 %s", exc)
            return fmt.MSG_UPSTREAM_ERROR
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sub2API QQ 助手：处理命令时出现未预期异常")
            return fmt.MSG_INTERNAL_ERROR


__all__ = ["Sub2ApiQqBotPlugin"]
