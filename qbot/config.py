"""插件配置：解析、默认值与校验。

配置项与 ``_conf_schema.json`` 一一对应。所有校验都在插件加载时完成，
不合法时抛出 ``ValueError``，让 AstrBot 在日志中明确报错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

from .log import logger

DEFAULT_SUB2API_BASE_URL = "http://sub2api:8080/api/v1"
DEFAULT_CHECKIN_BASE_URL = "http://sub2api-qiandao:8787/checkin"
DEFAULT_DRAW_BASE_URL = "http://sub2api-choujiang:8788/choujiang"

MIN_SECRET_LENGTH = 16
MIN_TIMEOUT, MAX_TIMEOUT = 5, 120
MIN_COOLDOWN, MAX_COOLDOWN = 0, 60

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}
_NUMBER = re.compile(r"[+-]?\d+")


@dataclass(frozen=True)
class PluginConfig:
    """经过校验的插件配置。"""

    sub2api_base_url: str = DEFAULT_SUB2API_BASE_URL
    sub2api_admin_email: str = ""
    sub2api_admin_password: str = ""
    allow_bind_admin: bool = False
    qiandao_base_url: str = DEFAULT_CHECKIN_BASE_URL
    choujiang_base_url: str = DEFAULT_DRAW_BASE_URL
    bot_shared_secret: str = ""
    qiandao_bot_secret: str = ""
    choujiang_bot_secret: str = ""
    request_timeout_seconds: int = 30
    command_cooldown_seconds: int = 2
    platform_whitelist: frozenset[str] = field(default_factory=frozenset)
    allowed_group_ids: frozenset[str] = field(default_factory=frozenset)
    currency_unit: str = "USD"
    mask_email_in_group: bool = True

    # ---------------------------------------------------------------- 派生属性

    @property
    def qiandao_secret(self) -> str:
        """签到服务使用的密钥：优先专用密钥，否则回退共享密钥。"""
        return self.qiandao_bot_secret or self.bot_shared_secret

    @property
    def choujiang_secret(self) -> str:
        """抽奖服务使用的密钥：优先专用密钥，否则回退共享密钥。"""
        return self.choujiang_bot_secret or self.bot_shared_secret

    @property
    def has_admin_credentials(self) -> bool:
        return bool(self.sub2api_admin_email and self.sub2api_admin_password)

    @property
    def request_timeout(self) -> float:
        return float(self.request_timeout_seconds)

    @property
    def platform_policy_text(self) -> str:
        """日志里展示的平台策略。"""
        if not self.platform_whitelist:
            return "不限制（任意平台）"
        return ", ".join(sorted(self.platform_whitelist))

    @property
    def group_policy_text(self) -> str:
        """日志里展示的群白名单策略。"""
        if not self.allowed_group_ids:
            return "不限制（任意群 + 私聊）"
        return ", ".join(sorted(self.allowed_group_ids))

    # ---------------------------------------------------------------- 构造入口

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "PluginConfig":
        """从 AstrBot 配置字典构造配置对象。

        Raises:
            ValueError: 任一配置项不合法。
        """
        raw: Mapping[str, Any] = data or {}
        group_ids = parse_group_ids(raw.get("allowed_group_ids"))
        if group_ids:
            logger.info("allowed_group_ids 已启用群白名单：%s", ", ".join(sorted(group_ids)))

        config = cls(
            sub2api_base_url=_url(raw, "sub2api_base_url", DEFAULT_SUB2API_BASE_URL),
            sub2api_admin_email=_text(raw, "sub2api_admin_email", ""),
            sub2api_admin_password=_text(raw, "sub2api_admin_password", ""),
            allow_bind_admin=_bool(raw, "allow_bind_admin", False),
            qiandao_base_url=_url(raw, "qiandao_base_url", DEFAULT_CHECKIN_BASE_URL),
            choujiang_base_url=_url(
                raw, "choujiang_base_url", DEFAULT_DRAW_BASE_URL
            ),
            bot_shared_secret=_secret(raw, "bot_shared_secret"),
            qiandao_bot_secret=_secret(raw, "qiandao_bot_secret"),
            choujiang_bot_secret=_secret(raw, "choujiang_bot_secret"),
            request_timeout_seconds=_int(
                raw, "request_timeout_seconds", 30, MIN_TIMEOUT, MAX_TIMEOUT
            ),
            command_cooldown_seconds=_int(
                raw, "command_cooldown_seconds", 2, MIN_COOLDOWN, MAX_COOLDOWN
            ),
            platform_whitelist=parse_platforms(raw.get("platform_whitelist")),
            allowed_group_ids=group_ids,
            currency_unit=_currency(raw, "currency_unit", "USD"),
            mask_email_in_group=_bool(raw, "mask_email_in_group", True),
        )
        return config


# --------------------------------------------------------------------- 解析工具


def normalize_id(value: Any) -> str:
    """把群号/平台名归一化成可比较的字符串。

    数字群号去掉前导零；OpenID 形式的群号（QQ 官方机器人 ``group_openid``）
    原样保留并统一小写，避免因为 ``int()`` 解析失败而被静默丢弃。
    """
    text = str(value or "").strip().lower()
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def _split_tokens(value: Any) -> list[str]:
    """把逗号（中英文）、顿号、换行、空白分隔的配置项拆成列表。"""
    if value is None:
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [str(int(value))]
    if isinstance(value, set | frozenset | list | tuple):
        return [str(item) for item in value]
    text = (
        str(value)
        .replace("，", ",")
        .replace("、", ",")
        .replace(";", ",")
        .replace("\n", ",")
        .replace(" ", ",")
        .replace("\t", ",")
    )
    return text.split(",")


def parse_group_ids(value: Any) -> frozenset[str]:
    """解析群号白名单。

    支持数字群号（OneBot/NapCat）与 OpenID 形式的群号（QQ 官方机器人
    ``group_openid``）。空项与纯空白会被忽略；``0``、``-5`` 这类数字会被丢弃并告警。
    """
    valid: set[str] = set()
    ignored: list[str] = []
    for item in _split_tokens(value):
        token = normalize_id(item)
        if not token:
            continue
        if _NUMBER.fullmatch(token) and int(token) <= 0:
            ignored.append(token)
            continue
        valid.add(token)
    if ignored:
        logger.warning(
            "allowed_group_ids 中以下项目不是合法群号，已忽略：%s", ", ".join(ignored)
        )
    return frozenset(valid)


def parse_platforms(value: Any) -> frozenset[str]:
    """解析平台白名单（``aiocqhttp`` / ``qq_official`` 等）。

    留空表示不限制平台：AstrBot 的事件里，平台 ID 是 ``aiocqhttp``（OneBot v11 /
    NapCat）或 ``qq_official``（QQ 官方机器人），两者都能正常收发纯文本命令。
    """
    valid = {
        token
        for token in (str(item).strip().lower() for item in _split_tokens(value))
        if token
    }
    return frozenset(valid)


def _text(raw: Mapping[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _bool(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ValueError(f"配置项 {key} 必须是布尔值（true/false），当前为 {value!r}")


def _int(
    raw: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        number = default
    else:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"配置项 {key} 必须是整数，当前为 {value!r}"
            ) from exc
    if not minimum <= number <= maximum:
        raise ValueError(
            f"配置项 {key} 必须在 {minimum}–{maximum} 之间，当前为 {number}"
        )
    return number


def _url(raw: Mapping[str, Any], key: str, default: str) -> str:
    text = _text(raw, key, default)
    if not text:
        raise ValueError(f"配置项 {key} 不能为空")
    try:
        parts = urlsplit(text)
        _ = parts.port  # 端口非法时抛 ValueError
    except ValueError as exc:
        raise ValueError(f"配置项 {key} 不是合法 URL：{text}") from exc
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"配置项 {key} 必须以 http:// 或 https:// 开头")
    if not parts.hostname:
        raise ValueError(f"配置项 {key} 缺少主机名")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValueError(f"配置项 {key} 不能包含用户名或密码")
    if parts.query:
        raise ValueError(f"配置项 {key} 不能包含查询参数（?）")
    if parts.fragment:
        raise ValueError(f"配置项 {key} 不能包含片段（#）")
    return text.rstrip("/")


def _secret(raw: Mapping[str, Any], key: str) -> str:
    text = _text(raw, key, "")
    if not text:
        return ""
    if len(text) < MIN_SECRET_LENGTH:
        raise ValueError(
            f"配置项 {key} 长度至少 {MIN_SECRET_LENGTH} 位，请使用随机字符串"
        )
    return text


def _currency(raw: Mapping[str, Any], key: str, default: str) -> str:
    text = _text(raw, key, default)
    return text or default


__all__ = [
    "DEFAULT_DRAW_BASE_URL",
    "DEFAULT_CHECKIN_BASE_URL",
    "DEFAULT_SUB2API_BASE_URL",
    "PluginConfig",
    "normalize_id",
    "parse_group_ids",
    "parse_platforms",
]

