"""文案与金额/时间格式化测试（对齐文档第 7、17 节）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from qbot import formatting as fmt


def test_amount_two_decimals() -> None:
    assert fmt.amount(0.3) == "0.30"
    assert fmt.amount("12.345") == "12.35"
    assert fmt.amount(Decimal("5.2")) == "5.20"
    assert fmt.amount(None) == "0.00"
    assert fmt.amount("bad") == "0.00"


def test_money_appends_unit() -> None:
    assert fmt.money(5.2, "USD") == "5.20 USD"
    assert fmt.money(5.2) == "5.20"


def test_mask_email() -> None:
    assert fmt.mask_email("alice@example.com") == "a***e@example.com"
    assert fmt.mask_email("ab@example.com") == "a*@example.com"
    assert fmt.mask_email("a@example.com") == "*@example.com"
    assert fmt.mask_email("not-an-email") == "***"


def test_display_email_respects_flag() -> None:
    assert fmt.display_email("alice@example.com", masked=True) == "a***e@example.com"
    assert fmt.display_email("alice@example.com", masked=False) == "alice@example.com"


def test_sanitize_strips_control_chars_and_truncates() -> None:
    assert fmt.sanitize("a\x00b\x1f\nc") == "a b c"
    long_text = "x" * 500
    result = fmt.sanitize(long_text)
    assert len(result) == 200
    assert result.endswith("…")


def test_format_time_localizes_to_shanghai() -> None:
    assert fmt.format_time("2026-11-01T00:12:00.000Z") == "08:12"
    assert fmt.format_time("2026-11-01T08:12:00+08:00") == "08:12"
    assert fmt.format_time("not-a-time") == ""
    assert fmt.format_time(None) == ""


@pytest.mark.parametrize(
    ("eligibility", "expected"),
    [
        ({"enabled": False, "eligible": True, "currentAmount": None}, fmt.ELIGIBILITY_DISABLED),
        ({"enabled": True, "eligible": True, "currentAmount": 12.34}, fmt.ELIGIBILITY_OK),
        ({"enabled": True, "eligible": False, "currentAmount": 3.2}, fmt.ELIGIBILITY_NOT_ENOUGH),
        ({"enabled": True, "eligible": True, "currentAmount": None}, fmt.ELIGIBILITY_CHECK_FAILED),
        ({"enabled": True, "eligible": False, "currentAmount": None, "error": "x"}, fmt.ELIGIBILITY_CHECK_FAILED),
        (None, fmt.ELIGIBILITY_UNKNOWN),
        ({}, fmt.ELIGIBILITY_UNKNOWN),
    ],
)
def test_eligibility_state(eligibility: object, expected: str) -> None:
    assert fmt.eligibility_state(eligibility) == expected


def test_eligibility_unit_prefers_service_value() -> None:
    assert fmt.eligibility_unit({"unit": "CNY"}, "USD") == "CNY"
    assert fmt.eligibility_unit({"unit": ""}, "USD") == "USD"
    assert fmt.eligibility_unit(None, "USD") == "USD"


def test_checkin_ok_with_and_without_label() -> None:
    entry = {"amount": 0.3, "unit": "USD", "rewardLabel": "连续签到奖励"}
    assert fmt.checkin_ok(entry, "USD") == "✅ 签到成功，+0.30 USD（连续签到奖励）"
    assert fmt.checkin_ok({"amount": 0.3}, "USD") == "✅ 签到成功，+0.30 USD"


def test_checkin_threshold_text_matches_doc() -> None:
    eligibility = {
        "enabled": True,
        "requiredAmount": 10,
        "currentAmount": 3.2,
        "remainingAmount": 6.8,
        "unit": "USD",
        "eligible": False,
    }
    assert (
        fmt.checkin_threshold(eligibility, "USD")
        == "今日消费 3.20/10.00 USD，未达到签到门槛（还差 6.80 USD）"
    )


def test_draw_threshold_text_matches_doc() -> None:
    eligibility = {
        "enabled": True,
        "requiredAmount": 10,
        "currentAmount": 3.2,
        "remainingAmount": 6.8,
        "unit": "USD",
        "eligible": False,
    }
    assert (
        fmt.draw_threshold(eligibility, "USD")
        == "今日消费 3.20/10.00 USD，未获得抽奖机会（还差 6.80 USD）"
    )


def test_threshold_computes_remaining_when_missing() -> None:
    eligibility = {
        "enabled": True,
        "requiredAmount": "10.00",
        "currentAmount": "7.25",
        "unit": "USD",
        "eligible": False,
    }
    assert fmt.checkin_threshold(eligibility, "USD").endswith("（还差 2.75 USD）")


def test_draw_ok_with_amount() -> None:
    prize = {"id": "p1", "label": "小锦鲤", "amount": 0.8}
    assert fmt.draw_ok(prize, "USD") == "🎉 恭喜抽中 0.80 USD（小锦鲤），已到账！"


def test_draw_ok_zero_amount() -> None:
    prize = {"id": "p0", "label": "谢谢参与", "amount": 0}
    assert fmt.draw_ok(prize, "USD") == "🎉 抽中「谢谢参与」，本次没有余额奖励"


def test_draw_already_uses_prize_info() -> None:
    prize = {"label": "好运到", "amount": 0.3}
    assert fmt.draw_already(prize, "USD") == "今天已经抽过了：0.30 USD（好运到）"


def test_draw_already_zero_amount() -> None:
    assert (
        fmt.draw_already({"label": "谢谢参与", "amount": 0}, "USD")
        == "今天已经抽过了：「谢谢参与」，本次没有余额奖励"
    )


def test_prize_label_is_truncated_and_cleaned() -> None:
    long_label = "锦" * 100
    text = fmt.draw_ok({"label": long_label, "amount": 1}, "USD")
    assert len(fmt.prize_label({"label": long_label})) == 40
    assert "錦" not in text and text.startswith("🎉 恭喜抽中 1.00 USD（")


def test_prize_label_falls_back_to_default() -> None:
    assert fmt.prize_label({}) == "谢谢参与"
    assert fmt.prize_label(None) == "谢谢参与"


def test_prize_prefers_draw_payload() -> None:
    prize = {"label": "奖池名", "amount": 0.8}
    draw = {"prizeLabel": "实际奖项", "amount": 0.5}
    assert fmt.prize_label(prize, draw) == "实际奖项"
    assert fmt.prize_amount(prize, draw) == Decimal("0.5")


def test_bind_ok_masks_email_and_shows_balance() -> None:
    text = fmt.bind_ok(
        email="alice@example.com",
        balance=5.2,
        unit="USD",
        masked=True,
    )
    expected = (
        "✅ 绑定成功\n"
        "账号：a***e@example.com\n"
        "余额：5.20 USD\n"
        "可用命令：/签到 /抽奖 /领测试额度 /查询 /解绑 /帮助"
    )
    assert text == expected


def test_bind_ok_with_failed_balance() -> None:
    text = fmt.bind_ok(email="alice@example.com", balance=None, unit="USD", masked=False)
    assert "余额：查询失败" in text


def test_unbind_ok() -> None:
    text = fmt.unbind_ok(email="alice@example.com", masked=True)
    assert text.startswith("✅ 已解绑 a***e@example.com")
    assert "历史签到/抽奖记录仍在服务端" in text


def test_trial_ok_with_and_without_balance() -> None:
    entry = {"amount": 1, "unit": "USD", "rewardLabel": "测试额度"}
    assert fmt.trial_ok(entry, "USD") == "✅ 领取成功，+1.00 USD 测试额度已到账"
    assert fmt.trial_ok(entry, "USD", 6.2) == (
        "✅ 领取成功，+1.00 USD 测试额度已到账\n当前余额：6.20 USD"
    )
    # 金额为 0 或缺失时也要能显示，不能抛异常
    assert fmt.trial_ok({}, "USD") == "✅ 领取成功，+0.00 USD 测试额度已到账"
    assert fmt.trial_ok(None, "USD") == "✅ 领取成功，+0.00 USD 测试额度已到账"


def test_trial_window_expired_texts() -> None:
    with_numbers = fmt.trial_window_expired(
        {"allowedDays": 3, "registeredDays": 10.2}, "USD"
    )
    assert with_numbers == (
        "账号注册已 10.20 天，超过可领取范围（注册 3.00 天内），无法领取测试额度"
    )

    only_allowed = fmt.trial_window_expired({"allowedDays": 3}, "USD")
    assert only_allowed == "账号注册超过 3.00 天，无法领取测试额度"

    fallback = fmt.trial_window_expired(None, "USD")
    assert fallback == "账号注册时间超过可领取范围，无法领取测试额度"


def test_cooldown_text() -> None:
    assert fmt.cooldown_text(3) == "操作太频繁，请 3 秒后再试"
    assert fmt.cooldown_text(0) == "操作太频繁，请 1 秒后再试"


def test_status_lines() -> None:
    assert fmt.status_header("a***e@example.com").splitlines()[0] == "📋 今日状态"
    assert fmt.balance_line("ok", {"balance": 5.2}, "USD") == "余额：5.20 USD"
    assert fmt.balance_line("error", None, "USD") == "余额：查询失败"

    payload = {
        "checkedIn": True,
        "entry": {"amount": 0.3, "unit": "USD", "createdAt": "2026-11-01T00:12:00.000Z"},
    }
    assert fmt.checkin_line("ok", payload, "USD") == "签到：已签到 +0.30 USD（08:12）"
    assert fmt.checkin_line("ok", {"checkedIn": False}, "USD") == "签到：未签到"
    assert fmt.checkin_line("error", None, "USD") == "签到：查询失败"
    assert fmt.checkin_line("unconfigured", None, "USD") == "签到：未配置"


def test_draw_line_variants() -> None:
    drawn = {"drawn": True, "draw": {"amount": 0.8, "prizeLabel": "小锦鲤"}}
    assert fmt.draw_line("ok", drawn, "USD") == "抽奖：已抽 0.80 USD（小锦鲤）"
    assert fmt.draw_line("ok", {"drawn": False}, "USD") == "抽奖：未抽奖"
    assert fmt.draw_line("ok", {"drawn": True, "prize": {"label": "谢谢参与", "amount": 0}}, "USD") == "抽奖：已抽（谢谢参与）"


@pytest.mark.parametrize(
    ("label", "state", "eligibility", "expected"),
    [
        ("签到门槛", "ok", {"enabled": False}, "签到门槛：未开启（不限消费）"),
        (
            "签到门槛",
            "ok",
            {
                "enabled": True,
                "requiredAmount": 5,
                "currentAmount": 12.34,
                "remainingAmount": 0,
                "eligible": True,
            },
            "签到门槛：5.00 USD（今日消费 12.34，已达标）",
        ),
        (
            "抽奖门槛",
            "ok",
            {
                "enabled": True,
                "requiredAmount": 10,
                "currentAmount": 3.2,
                "remainingAmount": 6.8,
                "eligible": False,
            },
            "抽奖门槛：10.00 USD（今日消费 3.20，还差 6.80）",
        ),
        (
            "抽奖门槛",
            "ok",
            {"enabled": True, "requiredAmount": 10, "currentAmount": None, "eligible": True},
            "抽奖门槛：10.00 USD（今日消费查询失败）",
        ),
        ("签到门槛", "error", None, "签到门槛：查询失败"),
        ("签到门槛", "unconfigured", None, "签到门槛：未配置"),
        ("签到门槛", "ok", None, "签到门槛：查询失败"),
    ],
)
def test_threshold_line(label: str, state: str, eligibility: object, expected: str) -> None:
    assert fmt.threshold_line(label, state, eligibility, "USD") == expected


def test_quota_line_variants() -> None:
    assert fmt.quota_line("ok", {"quota": {"remaining": 1}}) == "抽奖机会：剩余 1 次"
    assert fmt.quota_line("ok", {"quota": {"remaining": 0}}) == "抽奖机会：已用完"
    assert fmt.quota_line("ok", {}) == "抽奖机会：查询失败"
    assert fmt.quota_line("error", None) == "抽奖机会：查询失败"
    assert fmt.quota_line("unconfigured", None) == "抽奖机会：未配置"


def test_help_text_lists_all_commands() -> None:
    for command in ("/绑定", "/签到", "/抽奖", "/领测试额度", "/查询", "/解绑", "/帮助"):
        assert command in fmt.MSG_HELP
