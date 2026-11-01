"""命令业务逻辑：``/绑定`` ``/签到`` ``/抽奖`` ``/查询`` ``/解绑``。

这一层不依赖 AstrBot：输入是 QQ 号与消息文本，输出是回复文本，便于单元测试。
签到/抽奖的资格判断完全交给上游服务，插件只解释返回的 ``eligibility``，
不保存、不推断、不缓存消费门槛。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Iterable

from . import formatting as fmt
from .client import (
    BotError,
    BotServiceClient,
    NetworkError,
    ServiceNotConfigured,
    Sub2ApiClient,
    UpstreamError,
    UpstreamTimeout,
    UserError,
)
from .config import PluginConfig
from .log import logger
from .state import Binding, BindingStore, StateReadOnlyError

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_ROLES = {"user", "admin"}
SERVICE_OK = "ok"
SERVICE_ERROR = "error"
SERVICE_UNCONFIGURED = "unconfigured"


# --------------------------------------------------------------------- 解析工具


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.match(value or ""))


def extract_argument(message: str, names: Iterable[str]) -> str:
    """去掉消息开头的命令名，返回剩余参数文本。

    AstrBot 会去掉唤醒前缀（``/``），但命令名仍保留在 ``message_str`` 里。
    """
    text = (message or "").strip()
    lowered = text.lower()
    ordered = sorted({name for name in names if name}, key=len, reverse=True)
    for name in ordered:
        token = name.lower()
        if lowered == token:
            return ""
        if lowered.startswith(token):
            rest = text[len(name) :]
            if not rest:
                return ""
            if rest[0].isspace():
                return rest.strip()
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _mask_qq(qq: str) -> str:
    value = str(qq or "").strip()
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


def _mask_email_for_log(email: str) -> str:
    """日志里只保留邮箱域名（文档 10.4 隐私要求）。"""
    _, _, domain = (email or "").partition("@")
    return f"*@{domain}" if domain else "***"


# --------------------------------------------------------------------- 业务层


class BotService:
    """命令业务实现的集合。"""

    def __init__(
        self,
        config: PluginConfig,
        store: BindingStore,
        sub2api: Sub2ApiClient,
        qiandao: BotServiceClient,
        choujiang: BotServiceClient,
    ) -> None:
        self.config = config
        self.store = store
        self.sub2api = sub2api
        self.qiandao = qiandao
        self.choujiang = choujiang

    # ---------------------------------------------------------------- /帮助

    def help_text(self) -> str:
        return fmt.MSG_HELP

    # ---------------------------------------------------------------- /绑定

    async def bind(self, qq: str, argument: str, *, in_group: bool = False) -> str:
        self._ensure_available()
        tokens = (argument or "").split()
        if len(tokens) != 1:
            raise UserError(fmt.MSG_USAGE_BIND)
        email = normalize_email(tokens[0])
        if not is_valid_email(email):
            raise UserError(fmt.MSG_INVALID_EMAIL)

        current = await self.store.get(qq)
        if current is not None:
            if normalize_email(current.email) == email:
                raise UserError(fmt.MSG_ALREADY_BOUND_SAME)
            raise UserError(fmt.MSG_BIND_OTHER_FIRST)

        if not self.sub2api.credentials_configured:
            raise UserError(fmt.MSG_ADMIN_CREDS_MISSING)

        user = await self.sub2api.find_user_by_email(email)
        if user is None:
            raise UserError(fmt.MSG_USER_NOT_FOUND)
        self._validate_account(user)

        uid = str(user.get("id") or "").strip()
        if not uid:
            raise UserError(fmt.MSG_USER_NOT_FOUND)
        owner = await self.store.find_qq(uid)
        if owner is not None and owner != str(qq):
            raise UserError(fmt.MSG_UID_TAKEN)

        balance = user.get("balance")
        if balance is None:
            balance = await self._fetch_balance(uid)
        name = str(user.get("username") or "").strip() or email.partition("@")[0]
        binding = Binding(uid=uid, email=email, name=name, bound_at=int(time.time()))
        try:
            await self.store.bind(qq, binding)
        except StateReadOnlyError as exc:
            raise UserError(self._read_only_text(exc.reason)) from exc

        logger.info(
            "绑定成功：qq=%s uid=%s email=%s",
            _mask_qq(qq),
            uid,
            _mask_email_for_log(email),
        )
        return fmt.bind_ok(
            email=email,
            balance=balance,
            unit=self.config.currency_unit,
            masked=self._mask_in_group(in_group),
        )

    # ---------------------------------------------------------------- /签到

    async def checkin(self, qq: str) -> str:
        self._ensure_available()
        binding = await self._require_binding(qq)
        if not self.config.qiandao_secret:
            raise UserError(fmt.MSG_BOT_SECRET_MISSING)
        try:
            payload = await self.qiandao.checkin(binding.uid, binding.name)
        except ServiceNotConfigured as exc:
            raise UserError(fmt.MSG_BOT_SECRET_MISSING) from exc
        except (NetworkError, UpstreamTimeout) as exc:
            logger.warning("签到服务不可用：%s", exc)
            raise UserError(fmt.MSG_CHECKIN_UNAVAILABLE) from exc
        except UpstreamError as exc:
            logger.warning("签到失败：status=%s code=%s", exc.status, exc.code)
            raise UserError(
                self._bot_error_text(exc, label="签到服务", kind="checkin")
            ) from exc

        if payload.get("alreadyCheckedIn"):
            return fmt.MSG_CHECKIN_ALREADY
        if not payload.get("entry"):
            # 服务返回 200 但没有实际签到记录：若门槛未达标或无法校验，绝不静默放行
            self._guard_eligibility(payload.get("eligibility"), kind="checkin")
        unit = str(payload.get("unit") or self.config.currency_unit)
        return fmt.checkin_ok(payload.get("entry"), unit)

    # ---------------------------------------------------------------- /抽奖

    async def draw(self, qq: str) -> str:
        self._ensure_available()
        binding = await self._require_binding(qq)
        if not self.config.choujiang_secret:
            raise UserError(fmt.MSG_BOT_SECRET_MISSING)
        try:
            payload = await self.choujiang.draw(binding.uid, binding.name)
        except ServiceNotConfigured as exc:
            raise UserError(fmt.MSG_BOT_SECRET_MISSING) from exc
        except (NetworkError, UpstreamTimeout) as exc:
            logger.warning("抽奖服务不可用：%s", exc)
            raise UserError(fmt.MSG_DRAW_UNAVAILABLE) from exc
        except UpstreamError as exc:
            logger.warning("抽奖失败：status=%s code=%s", exc.status, exc.code)
            raise UserError(
                self._bot_error_text(exc, label="抽奖服务", kind="draw")
            ) from exc

        unit = str(payload.get("unit") or self.config.currency_unit)
        if payload.get("alreadyDrawn"):
            return fmt.draw_already(payload.get("prize"), unit, payload.get("draw"))
        if not payload.get("draw"):
            # 服务返回 200 但没有实际抽取记录：门槛未达标或无法校验时不允许放行
            self._guard_eligibility(payload.get("eligibility"), kind="draw")
        return fmt.draw_ok(payload.get("prize"), unit, payload.get("draw"))

    # ---------------------------------------------------------------- /查询

    async def status(self, qq: str, *, in_group: bool = False) -> str:
        self._ensure_available()
        binding = await self._require_binding(qq)
        fetched = await asyncio.gather(
            self._fetch_user(binding.uid),
            self._fetch_service_status(
                self.qiandao, self.config.qiandao_secret, binding.uid
            ),
            self._fetch_service_status(
                self.choujiang, self.config.choujiang_secret, binding.uid
            ),
        )
        (user_state, user), (checkin_state, checkin_payload), (
            draw_state,
            draw_payload,
        ) = fetched

        unit = self.config.currency_unit
        checkin_eligibility = (checkin_payload or {}).get("eligibility")
        draw_eligibility = (draw_payload or {}).get("eligibility")
        lines = [
            fmt.status_header(
                fmt.display_email(
                    binding.email, masked=self._mask_in_group(in_group)
                )
            ),
            fmt.balance_line(user_state, user, unit),
            fmt.checkin_line(checkin_state, checkin_payload, unit),
            fmt.draw_line(draw_state, draw_payload, unit),
            fmt.threshold_line("签到门槛", checkin_state, checkin_eligibility, unit),
            fmt.threshold_line("抽奖门槛", draw_state, draw_eligibility, unit),
            fmt.quota_line(draw_state, draw_payload),
        ]
        return "\n".join(lines)

    # ---------------------------------------------------------------- /解绑

    async def unbind(self, qq: str, *, in_group: bool = False) -> str:
        self._ensure_available()
        try:
            binding = await self.store.remove(qq)
        except StateReadOnlyError as exc:
            raise UserError(self._read_only_text(exc.reason)) from exc
        if binding is None:
            raise UserError(fmt.MSG_NOT_BOUND)
        logger.info(
            "解绑：qq=%s email=%s", _mask_qq(qq), _mask_email_for_log(binding.email)
        )
        return fmt.unbind_ok(
            email=binding.email, masked=self._mask_in_group(in_group)
        )

    # ---------------------------------------------------------------- 内部方法

    def _ensure_available(self) -> None:
        reason = self.store.read_only_reason
        if reason is not None:
            raise UserError(self._read_only_text(reason))

    def _read_only_text(self, reason: str | None) -> str:
        if reason == "version":
            return fmt.MSG_STATE_UNSUPPORTED
        return fmt.MSG_STATE_CORRUPT

    async def _require_binding(self, qq: str) -> Binding:
        binding = await self.store.get(qq)
        if binding is None:
            raise UserError(fmt.MSG_UNBOUND)
        return binding

    def _mask_in_group(self, in_group: bool) -> bool:
        return bool(in_group and self.config.mask_email_in_group)

    def _validate_account(self, user: dict[str, Any]) -> None:
        status = str(user.get("status") or "").strip().lower()
        if status and status != "active":
            raise UserError(fmt.MSG_USER_INACTIVE)
        role = str(user.get("role") or "user").strip().lower()
        if role not in ALLOWED_ROLES:
            raise UserError(fmt.MSG_ROLE_REJECTED)
        if role == "admin" and not self.config.allow_bind_admin:
            raise UserError(fmt.MSG_ADMIN_REJECTED)

    async def _fetch_balance(self, uid: str) -> Any:
        try:
            detail = await self.sub2api.get_user(uid)
        except BotError as exc:
            logger.warning("绑定后查询余额失败：%s", exc)
            return None
        return detail.get("balance")

    async def _fetch_user(self, uid: str) -> tuple[str, dict[str, Any] | None]:
        try:
            user = await self.sub2api.get_user(uid)
        except BotError as exc:
            logger.warning("查询 Sub2API 用户失败：%s", exc)
            return SERVICE_ERROR, None
        return SERVICE_OK, user

    async def _fetch_service_status(
        self,
        client: BotServiceClient,
        secret: str,
        uid: str,
    ) -> tuple[str, dict[str, Any] | None]:
        if not secret:
            return SERVICE_UNCONFIGURED, None
        try:
            payload = await client.status(uid)
        except BotError as exc:
            logger.warning("%s 状态查询失败：%s", client.label, exc)
            return SERVICE_ERROR, None
        return SERVICE_OK, payload

    def _guard_eligibility(self, eligibility: Any, *, kind: str) -> None:
        """服务返回 200 但没有实际记录时的兜底：门槛不达标或无法校验都不放行。"""
        state = fmt.eligibility_state(eligibility)
        if state == fmt.ELIGIBILITY_NOT_ENOUGH:
            unit = self.config.currency_unit
            if kind == "checkin":
                raise UserError(fmt.checkin_threshold(eligibility, unit))
            raise UserError(fmt.draw_threshold(eligibility, unit))
        if state == fmt.ELIGIBILITY_CHECK_FAILED:
            raise UserError(
                fmt.MSG_CHECKIN_THRESHOLD_FAILED
                if kind == "checkin"
                else fmt.MSG_DRAW_THRESHOLD_FAILED
            )

    def _bot_error_text(self, exc: UpstreamError, *, label: str, kind: str) -> str:
        """把上游错误翻译成用户可读文案。"""
        status = exc.status or 0
        code = (exc.code or "").strip().lower()
        payload = exc.payload if isinstance(exc.payload, dict) else {}

        if code == "daily_spend_not_enough":
            eligibility = payload.get("eligibility")
            state = fmt.eligibility_state(eligibility)
            if state == fmt.ELIGIBILITY_CHECK_FAILED:
                return (
                    fmt.MSG_CHECKIN_THRESHOLD_FAILED
                    if kind == "checkin"
                    else fmt.MSG_DRAW_THRESHOLD_FAILED
                )
            unit = self.config.currency_unit
            if kind == "checkin":
                return fmt.checkin_threshold(eligibility, unit)
            return fmt.draw_threshold(eligibility, unit)

        if status in (401, 403) or code in {"invalid_bot_secret", "bot_disabled"}:
            return f"{label}未配置或密钥错误，请联系管理员"
        if status == 400 or code == "invalid_user_id":
            return fmt.MSG_INVALID_USER_ID
        if status == 404 or code == "user_not_found":
            return fmt.MSG_USER_GONE
        if status == 423 or code == "compliance_required":
            return fmt.MSG_COMPLIANCE
        if status == 504 or code == "upstream_timeout":
            return fmt.MSG_TIMEOUT_UPSTREAM
        if status >= 500 or code in {"upstream_error", "internal_error"}:
            return fmt.MSG_UPSTREAM_ERROR
        message = fmt.sanitize(exc.message)
        return message or fmt.MSG_UPSTREAM_ERROR


__all__ = [
    "BotService",
    "EMAIL_PATTERN",
    "SERVICE_ERROR",
    "SERVICE_OK",
    "SERVICE_UNCONFIGURED",
    "extract_argument",
    "is_valid_email",
    "normalize_email",
]
