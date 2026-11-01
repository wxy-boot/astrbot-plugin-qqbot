"""配置解析与校验测试。"""

from __future__ import annotations

import pytest

from qbot.config import PluginConfig, parse_group_ids


def test_defaults_when_mapping_empty() -> None:
    config = PluginConfig.from_mapping({})
    assert config.sub2api_base_url == "http://sub2api:8080/api/v1"
    assert config.qiandao_base_url == "http://sub2api-qiandao:8787/checkin"
    assert config.choujiang_base_url == "http://sub2api-choujiang:8788/choujiang"
    assert config.request_timeout_seconds == 30
    assert config.command_cooldown_seconds == 2
    assert config.currency_unit == "USD"
    assert config.mask_email_in_group is True
    assert config.allow_bind_admin is False
    assert config.allowed_group_ids == frozenset()


def test_none_mapping_is_tolerated() -> None:
    assert PluginConfig.from_mapping(None).request_timeout_seconds == 30


def test_trailing_slash_is_stripped() -> None:
    config = PluginConfig.from_mapping(
        {"sub2api_base_url": "https://api.example.com/api/v1/"}
    )
    assert config.sub2api_base_url == "https://api.example.com/api/v1"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://sub2api:8080/api/v1",
        "sub2api:8080/api/v1",
        "http://user:pass@sub2api:8080/api/v1",
        "http://sub2api:8080/api/v1?debug=1",
        "http://sub2api:8080/api/v1#frag",
        "http://",
        "",
    ],
)
def test_invalid_url_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        PluginConfig.from_mapping({"sub2api_base_url": url})


def test_timeout_range_checked() -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        PluginConfig.from_mapping({"request_timeout_seconds": 4})
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        PluginConfig.from_mapping({"request_timeout_seconds": 121})
    assert PluginConfig.from_mapping({"request_timeout_seconds": 5}).request_timeout_seconds == 5
    assert PluginConfig.from_mapping({"request_timeout_seconds": "60"}).request_timeout_seconds == 60


def test_cooldown_range_checked() -> None:
    with pytest.raises(ValueError, match="command_cooldown_seconds"):
        PluginConfig.from_mapping({"command_cooldown_seconds": 61})
    assert PluginConfig.from_mapping({"command_cooldown_seconds": 0}).command_cooldown_seconds == 0


def test_timeout_rejects_non_integer() -> None:
    with pytest.raises(ValueError, match="必须是整数"):
        PluginConfig.from_mapping({"request_timeout_seconds": "abc"})


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on", True])
def test_bool_parsing_true(value: object) -> None:
    assert PluginConfig.from_mapping({"mask_email_in_group": value}).mask_email_in_group is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", False])
def test_bool_parsing_false(value: object) -> None:
    assert PluginConfig.from_mapping({"mask_email_in_group": value}).mask_email_in_group is False


def test_bool_parsing_invalid() -> None:
    with pytest.raises(ValueError, match="布尔值"):
        PluginConfig.from_mapping({"allow_bind_admin": "maybe"})


def test_secret_too_short_rejected() -> None:
    with pytest.raises(ValueError, match="bot_shared_secret"):
        PluginConfig.from_mapping({"bot_shared_secret": "short"})


def test_empty_secret_allowed() -> None:
    config = PluginConfig.from_mapping({"bot_shared_secret": ""})
    assert config.bot_shared_secret == ""
    assert config.qiandao_secret == ""
    assert config.choujiang_secret == ""


def test_dedicated_secret_overrides_shared() -> None:
    config = PluginConfig.from_mapping(
        {
            "bot_shared_secret": "shared-secret-0123456789",
            "qiandao_bot_secret": "checkin-secret-0123456789",
        }
    )
    assert config.qiandao_secret == "checkin-secret-0123456789"
    assert config.choujiang_secret == "shared-secret-0123456789"


def test_admin_credentials_flag() -> None:
    assert PluginConfig.from_mapping({}).has_admin_credentials is False
    assert (
        PluginConfig.from_mapping(
            {"sub2api_admin_email": "bot@example.com", "sub2api_admin_password": "x"}
        ).has_admin_credentials
        is True
    )


def test_parse_group_ids_supports_chinese_comma() -> None:
    assert parse_group_ids("123456，789012, 345678") == frozenset(
        {"123456", "789012", "345678"}
    )


def test_parse_group_ids_accepts_openid_groups() -> None:
    """QQ 官方机器人的 group_openid 不是数字，不能被丢掉。"""
    assert parse_group_ids("A1B2C3D4E5F6, 123456") == frozenset(
        {"a1b2c3d4e5f6", "123456"}
    )


def test_parse_group_ids_drops_non_positive_numbers() -> None:
    assert parse_group_ids("123456,-5,0,789012") == frozenset({"123456", "789012"})


def test_parse_group_ids_empty() -> None:
    assert parse_group_ids("") == frozenset()
    assert parse_group_ids("   ") == frozenset()
    assert parse_group_ids(None) == frozenset()


def test_parse_group_ids_normalizes_leading_zeros() -> None:
    assert parse_group_ids("0123") == frozenset({"123"})


def test_allowed_group_ids_config() -> None:
    config = PluginConfig.from_mapping({"allowed_group_ids": "100,200"})
    assert config.allowed_group_ids == frozenset({"100", "200"})


def test_platform_whitelist_defaults_to_unrestricted() -> None:
    """默认不限制平台：NapCat(aiocqhttp) 与 QQ 官方机器人(qq_official) 都能用。"""
    config = PluginConfig.from_mapping({})
    assert config.platform_whitelist == frozenset()
    assert config.platform_policy_text == "不限制（任意平台）"


def test_platform_whitelist_parsed_and_normalized() -> None:
    config = PluginConfig.from_mapping({"platform_whitelist": " Aiocqhttp，QQ_Official "})
    assert config.platform_whitelist == frozenset({"aiocqhttp", "qq_official"})


def test_group_policy_text() -> None:
    assert PluginConfig.from_mapping({}).group_policy_text.startswith("不限制")
    config = PluginConfig.from_mapping({"allowed_group_ids": "200,100"})
    assert config.group_policy_text == "100, 200"


def test_currency_unit_fallback() -> None:
    assert PluginConfig.from_mapping({"currency_unit": "  "}).currency_unit == "USD"
    assert PluginConfig.from_mapping({"currency_unit": "CNY"}).currency_unit == "CNY"
