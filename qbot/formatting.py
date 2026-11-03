"""用户可见文案与格式化工具。

所有回复文本集中在这里，便于单元测试与统一调整；命令处理器不直接拼接文案。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

try:  # pragma: no cover - Windows 缺少 tzdata 时退化为固定 +08:00
    TIMEZONE = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    TIMEZONE = timezone(timedelta(hours=8))

MONEY_STEP = Decimal("0.01")
MAX_MESSAGE_LENGTH = 200
MAX_PRIZE_LABEL = 40
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# ------------------------------------------------------------------ 静态文案

MSG_HELP = """📖 Sub2API QQ 助手
/绑定 邮箱   绑定站点账号（一个 QQ 一个账号）
/签到        每日签到
/抽奖        当日消费达标后抽奖
/领测试额度  领取新人测试额度（仅注册初期，一个账号一次）
/查询        查看余额、签到、抽奖、消费
/解绑        解除当前绑定
/帮助        显示本帮助"""

MSG_UNBOUND = "你还没有绑定账号，请先发送 /绑定 你的注册邮箱"
MSG_NOT_BOUND = "你还没有绑定账号，使用 /绑定 邮箱"
MSG_USAGE_BIND = "用法：/绑定 你的注册邮箱，例如 /绑定 alice@example.com"
MSG_INVALID_EMAIL = "邮箱格式不正确，请检查后重试（例如 alice@example.com）"
MSG_ALREADY_BOUND_SAME = "你已经绑定该账号"
MSG_BIND_OTHER_FIRST = "请先 /解绑，再绑定新账号"
MSG_USER_NOT_FOUND = "未找到该邮箱对应的 Sub2API 账号，请确认注册邮箱"
MSG_USER_INACTIVE = "账号当前不是正常启用状态"
MSG_ROLE_REJECTED = "该账号的角色不支持绑定，请联系站点管理员"
MSG_ADMIN_REJECTED = "该账号是管理员账号，插件默认不允许绑定"
MSG_UID_TAKEN = "该账号已绑定其他 QQ，请联系管理员解绑"
MSG_ADMIN_CREDS_MISSING = "管理员凭据未配置，请联系站点管理员"
MSG_BOT_SECRET_MISSING = "未配置机器人密钥，请联系站点管理员"
MSG_STATE_CORRUPT = "绑定数据损坏，请联系管理员恢复备份"
MSG_STATE_UNSUPPORTED = "绑定数据版本高于插件支持版本，请升级插件"
MSG_INTERNAL_ERROR = "插件内部错误，请稍后再试；如持续出现请联系管理员"
MSG_UPSTREAM_ERROR = "上游服务异常，请稍后再试"
MSG_TIMEOUT_UPSTREAM = "请求超时，请稍后再试"
MSG_COMPLIANCE = "站点管理员尚未完成合规确认"
MSG_USER_GONE = "绑定账号已不存在，请重新绑定"
MSG_INVALID_USER_ID = "账号数据异常，请联系管理员"
MSG_CHECKIN_THRESHOLD_FAILED = "签到门槛校验失败，请联系管理员检查消费查询配置"
MSG_DRAW_THRESHOLD_FAILED = "抽奖门槛校验失败，请联系管理员检查消费查询配置"
MSG_CHECKIN_ALREADY = "今天已经签到过了，明天再来~"
MSG_CHECKIN_UNAVAILABLE = "签到服务暂时不可用，请稍后再试；可先用 /查询 确认结果"
MSG_DRAW_UNAVAILABLE = "抽奖服务暂时不可用，请稍后再试；可先用 /查询 确认结果"
MSG_TRIAL_ALREADY = "你已经领过测试额度了，每个账号限领一次"
MSG_TRIAL_DISABLED = "测试额度活动暂未开启，请联系管理员"
MSG_TRIAL_AUTH = "测试额度服务未配置或密钥错误，请联系管理员"
MSG_TRIAL_NOT_DEPLOYED = "签到服务还没有提供测试额度接口，请升级签到服务或联系管理员"
MSG_TRIAL_REGISTRATION_UNKNOWN = (
    "无法确认账号注册时间，暂时不能领取测试额度，请联系管理员"
)
MSG_TRIAL_UNAVAILABLE = (
    "测试额度服务暂时不可用，请稍后再试；超时也可能已经到账，可先用 /查询 确认余额"
)
MSG_COOLDOWN = "操作太频繁，请 {seconds} 秒后再试"
MSG_SERVICE_UNCONFIGURED = "未配置"
MSG_QUERY_FAILED = "查询失败"
MSG_BALANCE_FAILED = "余额：查询失败"


# ------------------------------------------------------------------ 基础格式化


def to_decimal(value: Any) -> Decimal | None:
    """把任意来源的金额转换成 Decimal，失败返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def amount(value: Any) -> str:
    """金额字符串，固定两位小数（不做货币单位拼接）。"""
    number = to_decimal(value)
    if number is None:
        return "0.00"
    try:
        return str(number.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return str(number)


def money(value: Any, unit: str = "") -> str:
    """带单位的金额，例如 ``0.30 USD``。"""
    text = amount(value)
    unit = (unit or "").strip()
    return f"{text} {unit}" if unit else text


def sanitize(text: Any, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """去除控制字符、压缩空白并截断。"""
    if text is None:
        return ""
    cleaned = _CONTROL_CHARS.sub(" ", str(text))
    cleaned = " ".join(cleaned.split())
    if limit and len(cleaned) > limit:
        cleaned = cleaned[: max(limit - 1, 0)] + "…"
    return cleaned


def mask_email(email: str) -> str:
    """邮箱脱敏：``alice@example.com`` → ``a***e@example.com``。"""
    value = (email or "").strip()
    if "@" not in value:
        return "***" if value else ""
    local, _, domain = value.partition("@")
    if len(local) <= 1:
        masked = "*" * max(len(local), 1)
    elif len(local) == 2:
        masked = f"{local[0]}*"
    else:
        masked = f"{local[0]}***{local[-1]}"
    return f"{masked}@{domain}"


def display_email(email: str, *, masked: bool) -> str:
    return mask_email(email) if masked else (email or "")


def format_time(value: Any) -> str:
    """把 ISO 时间转换为 ``Asia/Shanghai`` 的 ``HH:mm``；无法解析时返回空串。"""
    if not value:
        return ""
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        parsed = parsed.astimezone(TIMEZONE)
    except (OverflowError, OSError, ValueError):  # pragma: no cover
        pass
    return parsed.strftime("%H:%M")


def cooldown_text(seconds: int) -> str:
    return MSG_COOLDOWN.format(seconds=max(int(seconds), 1))


# ------------------------------------------------------------------ 门槛解释

ELIGIBILITY_UNKNOWN = "unknown"
ELIGIBILITY_DISABLED = "disabled"
ELIGIBILITY_OK = "ok"
ELIGIBILITY_NOT_ENOUGH = "not_enough"
ELIGIBILITY_CHECK_FAILED = "check_failed"


def eligibility_state(eligibility: Any) -> str:
    """把服务返回的 ``eligibility`` 归一化为五种状态。

    - ``disabled``：服务未开启门槛，任何绑定用户都可以使用；
    - ``ok``：门槛已达标；
    - ``not_enough``：开启了门槛但消费不足；
    - ``check_failed``：开启了门槛但消费查询失败（不能放行）；
    - ``unknown``：服务没有返回门槛信息。
    """
    if not isinstance(eligibility, dict) or "enabled" not in eligibility:
        return ELIGIBILITY_UNKNOWN
    if not eligibility.get("enabled"):
        return ELIGIBILITY_DISABLED
    if eligibility.get("currentAmount") is None:
        return ELIGIBILITY_CHECK_FAILED
    return ELIGIBILITY_OK if eligibility.get("eligible") else ELIGIBILITY_NOT_ENOUGH


def eligibility_unit(eligibility: Any, default_unit: str) -> str:
    if isinstance(eligibility, dict):
        unit = eligibility.get("unit")
        if isinstance(unit, str) and unit.strip():
            return unit.strip()
    return default_unit


def eligibility_amounts(eligibility: Any) -> tuple[Decimal, Decimal | None, Decimal]:
    """返回 (门槛金额, 当日消费, 还差金额)。"""
    data = eligibility if isinstance(eligibility, dict) else {}
    required = to_decimal(data.get("requiredAmount")) or Decimal("0")
    current = to_decimal(data.get("currentAmount"))
    remaining = to_decimal(data.get("remainingAmount"))
    if remaining is None:
        remaining = max(required - current, Decimal("0")) if current is not None else Decimal("0")
    return required, current, remaining


# ------------------------------------------------------------------ 命令回复


def bind_ok(*, email: str, balance: Any, unit: str, masked: bool) -> str:
    balance_text = MSG_QUERY_FAILED if balance is None else money(balance, unit)
    return (
        "✅ 绑定成功\n"
        f"账号：{display_email(email, masked=masked)}\n"
        f"余额：{balance_text}\n"
        "可用命令：/签到 /抽奖 /领测试额度 /查询 /解绑 /帮助"
    )


def unbind_ok(*, email: str, masked: bool) -> str:
    return (
        f"✅ 已解绑 {display_email(email, masked=masked)}\n"
        "绑定关系已删除，历史签到/抽奖记录仍在服务端。"
    )


def checkin_ok(entry: Any, unit: str) -> str:
    data = entry if isinstance(entry, dict) else {}
    label = sanitize(data.get("rewardLabel"), MAX_PRIZE_LABEL)
    entry_unit = data.get("unit") or unit
    text = f"✅ 签到成功，+{money(data.get('amount'), entry_unit)}"
    return f"{text}（{label}）" if label else text


def checkin_threshold(eligibility: Any, unit: str) -> str:
    required, current, remaining = eligibility_amounts(eligibility)
    unit = eligibility_unit(eligibility, unit)
    return (
        f"今日消费 {amount(current)}/{amount(required)} {unit}"
        f"，未达到签到门槛（还差 {amount(remaining)} {unit}）"
    )


def draw_threshold(eligibility: Any, unit: str) -> str:
    required, current, remaining = eligibility_amounts(eligibility)
    unit = eligibility_unit(eligibility, unit)
    return (
        f"今日消费 {amount(current)}/{amount(required)} {unit}"
        f"，未获得抽奖机会（还差 {amount(remaining)} {unit}）"
    )


def prize_label(prize: Any, draw: Any = None) -> str:
    data = draw if isinstance(draw, dict) else {}
    prize_data = prize if isinstance(prize, dict) else {}
    label = data.get("prizeLabel") or prize_data.get("label")
    return sanitize(label, MAX_PRIZE_LABEL) or "谢谢参与"


def prize_amount(prize: Any, draw: Any = None) -> Decimal | None:
    data = draw if isinstance(draw, dict) else {}
    prize_data = prize if isinstance(prize, dict) else {}
    value = data.get("amount")
    if value is None:
        value = prize_data.get("amount")
    return to_decimal(value)


def prize_unit(prize: Any, draw: Any, default_unit: str) -> str:
    for source in (draw, prize):
        if isinstance(source, dict) and isinstance(source.get("unit"), str):
            unit = source["unit"].strip()
            if unit:
                return unit
    return default_unit


def draw_ok(prize: Any, unit: str, draw: Any = None) -> str:
    label = prize_label(prize, draw)
    gained = prize_amount(prize, draw)
    unit = prize_unit(prize, draw, unit)
    if gained is not None and gained > 0:
        return f"🎉 恭喜抽中 {money(gained, unit)}（{label}），已到账！"
    return f"🎉 抽中「{label}」，本次没有余额奖励"


def draw_already(prize: Any, unit: str, draw: Any = None) -> str:
    label = prize_label(prize, draw)
    gained = prize_amount(prize, draw)
    unit = prize_unit(prize, draw, unit)
    if gained is not None and gained > 0:
        return f"今天已经抽过了：{money(gained, unit)}（{label}）"
    return f"今天已经抽过了：「{label}」，本次没有余额奖励"


# ------------------------------------------------------------------ /领测试额度


def trial_ok(entry: Any, unit: str, balance: Any = None) -> str:
    """领取成功：金额与单位以签到服务返回为准，余额可选。"""
    data = entry if isinstance(entry, dict) else {}
    entry_unit = data.get("unit") or unit
    text = f"✅ 领取成功，+{money(data.get('amount'), entry_unit)} 测试额度已到账"
    if balance is not None:
        text = f"{text}\n当前余额：{money(balance, unit)}"
    return text


def trial_window_expired(eligibility: Any, unit: str = "") -> str:
    """注册时间超过允许天数时的提示，尽量带上服务返回的天数。"""
    data = eligibility if isinstance(eligibility, dict) else {}
    allowed = data.get("allowedDays")
    registered = data.get("registeredDays")

    def _days(value: Any) -> str:
        number = to_decimal(value)
        if number is None:
            return ""
        return amount(number)

    allowed_text = _days(allowed)
    registered_text = _days(registered)
    if allowed_text and registered_text:
        return (
            f"账号注册已 {registered_text} 天，超过可领取范围"
            f"（注册 {allowed_text} 天内），无法领取测试额度"
        )
    if allowed_text:
        return f"账号注册超过 {allowed_text} 天，无法领取测试额度"
    return "账号注册时间超过可领取范围，无法领取测试额度"


# ------------------------------------------------------------------ /查询 卡片


def status_header(email: str) -> str:
    return f"📋 今日状态\n账号：{email}"


def balance_line(state: str, user: Any, unit: str) -> str:
    if state != "ok" or not isinstance(user, dict):
        return MSG_BALANCE_FAILED
    balance = user.get("balance")
    if balance is None:
        return MSG_BALANCE_FAILED
    return f"余额：{money(balance, unit)}"


def checkin_line(state: str, payload: Any, unit: str) -> str:
    if state == "unconfigured":
        return f"签到：{MSG_SERVICE_UNCONFIGURED}"
    if state != "ok" or not isinstance(payload, dict):
        return f"签到：{MSG_QUERY_FAILED}"
    if not payload.get("checkedIn"):
        return "签到：未签到"
    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
    entry_unit = entry.get("unit") or unit
    text = f"签到：已签到 +{money(entry.get('amount'), entry_unit)}"
    when = format_time(entry.get("createdAt"))
    return f"{text}（{when}）" if when else text


def draw_line(state: str, payload: Any, unit: str) -> str:
    if state == "unconfigured":
        return f"抽奖：{MSG_SERVICE_UNCONFIGURED}"
    if state != "ok" or not isinstance(payload, dict):
        return f"抽奖：{MSG_QUERY_FAILED}"
    if not payload.get("drawn"):
        return "抽奖：未抽奖"
    draw = payload.get("draw")
    prize = payload.get("prize")
    label = prize_label(prize, draw)
    gained = prize_amount(prize, draw)
    gained_unit = prize_unit(prize, draw, unit)
    if gained is not None and gained > 0:
        return f"抽奖：已抽 {money(gained, gained_unit)}（{label}）"
    return f"抽奖：已抽（{label}）"


def threshold_line(label: str, state: str, eligibility: Any, unit: str) -> str:
    """门槛行。``state`` 是服务查询状态（ok/error/unconfigured）。"""
    if state == "unconfigured":
        return f"{label}：{MSG_SERVICE_UNCONFIGURED}"
    if state != "ok":
        return f"{label}：{MSG_QUERY_FAILED}"
    kind = eligibility_state(eligibility)
    if kind == ELIGIBILITY_UNKNOWN:
        return f"{label}：{MSG_QUERY_FAILED}"
    if kind == ELIGIBILITY_DISABLED:
        return f"{label}：未开启（不限消费）"
    unit = eligibility_unit(eligibility, unit)
    required, current, remaining = eligibility_amounts(eligibility)
    if kind == ELIGIBILITY_OK:
        return f"{label}：{money(required, unit)}（今日消费 {amount(current)}，已达标）"
    if kind == ELIGIBILITY_NOT_ENOUGH:
        return (
            f"{label}：{money(required, unit)}"
            f"（今日消费 {amount(current)}，还差 {amount(remaining)}）"
        )
    return f"{label}：{money(required, unit)}（今日消费查询失败）"


def quota_line(state: str, payload: Any) -> str:
    if state == "unconfigured":
        return f"抽奖机会：{MSG_SERVICE_UNCONFIGURED}"
    if state != "ok" or not isinstance(payload, dict):
        return f"抽奖机会：{MSG_QUERY_FAILED}"
    quota = payload.get("quota")
    if not isinstance(quota, dict):
        return f"抽奖机会：{MSG_QUERY_FAILED}"
    remaining = quota.get("remaining")
    try:
        remaining_number = int(str(remaining).strip())
    except (TypeError, ValueError):
        return f"抽奖机会：{MSG_QUERY_FAILED}"
    if remaining_number > 0:
        return f"抽奖机会：剩余 {remaining_number} 次"
    return "抽奖机会：已用完"


__all__ = [
    "ELIGIBILITY_CHECK_FAILED",
    "ELIGIBILITY_DISABLED",
    "ELIGIBILITY_NOT_ENOUGH",
    "ELIGIBILITY_OK",
    "ELIGIBILITY_UNKNOWN",
    "amount",
    "balance_line",
    "bind_ok",
    "checkin_line",
    "checkin_ok",
    "checkin_threshold",
    "cooldown_text",
    "display_email",
    "draw_already",
    "draw_line",
    "draw_ok",
    "draw_threshold",
    "eligibility_amounts",
    "eligibility_state",
    "eligibility_unit",
    "format_time",
    "mask_email",
    "money",
    "quota_line",
    "sanitize",
    "status_header",
    "threshold_line",
    "to_decimal",
    "trial_ok",
    "trial_window_expired",
    "unbind_ok",
]
