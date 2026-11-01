"""插件配置：解析、默认值与校验。

配置项与 ``_conf_schema.json`` 一一对应。所有校验都在插件加载时完成，
不合法时抛出 ``ValueError``，让 AstrBot 在日志中明确报错。
"""

from __future__ import annotations

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
    allowed_group_ids: frozenset[int] = field(default_factory=frozenset)
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

    # ---------------------------------------------------------------- 构造入口

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "PluginConfig":
        """从 AstrBot 配置字典构造配置对象。

        Raises:
            ValueError: 任一配置项不合法。
        """
        raw: Mapping[str, Any] = data or {}
        group_ids, invalid_ids = parse_group_ids(raw.get("allowed_group_ids"))
        if invalid_ids:
            logger.warning(
                "allowed_group_ids 中以下项目不是合法群号，已忽略：%s",
                ", ".join(invalid_ids),
            )

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
            allowed_group_ids=group_ids,
            currency_unit=_currency(raw, "currency_unit", "USD"),
            mask_email_in_group=_bool(raw, "mask_email_in_group", True),
        )
        return config


# --------------------------------------------------------------------- 解析工具


def parse_group_ids(value: Any) -> tuple[frozenset[int], list[str]]:
    """解析群号白名单。

    支持英文/中文逗号、空白分隔。非法项会被忽略并返回，交由调用方告警。

    Returns:
        (合法群号集合, 非法原始项列表)
    """
    if value is None:
        return frozenset(), []
    if isinstance(value, int | float):
        items = [str(int(value))]
    elif isinstance(value, set | frozenset | list | tuple):
        items = [str(item) for item in value]
    else:
        text = str(value).replace("，", ",").replace("、", ",")
        items = text.replace("\n", ",").replace(" ", ",").split(",")

    valid: set[int] = set()
    invalid: list[str] = []
    for item in items:
        token = str(item).strip()
        if not token:
            continue
        try:
            number = int(token)
        except ValueError:
            invalid.append(token)
            continue
        if number <= 0:
            invalid.append(token)
            continue
        valid.add(number)
    return frozenset(valid), invalid


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
    "parse_group_ids",
]

