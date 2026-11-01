"""验证插件能按 AstrBot 的方式载入：``data.plugins.<目录名>.main``。

AstrBot 以 ``__import__(f"data.plugins.{root_dir_name}.main")`` 的形式加载插件，
此时 ``main.py`` 走的是 ``from .qbot import ...`` 分支。这里复制一份插件到
临时目录并用同样的模块路径导入，确保两种导入方式都可用。
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("astrbot")

from helpers import BASE_CONFIG  # noqa: E402

PLUGIN_DIR_NAME = "astrbot_plugin_sub2api_qqbot"
MODULE_PATH = f"data.plugins.{PLUGIN_DIR_NAME}.main"


def test_plugin_loads_as_nested_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target = tmp_path / "data" / "plugins" / PLUGIN_DIR_NAME
    target.mkdir(parents=True)
    shutil.copy2(repo_root / "main.py", target / "main.py")
    shutil.copytree(
        repo_root / "qbot",
        target / "qbot",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    for name in list(sys.modules):
        if name == "data" or name.startswith("data.plugins"):
            sys.modules.pop(name, None)

    module = importlib.import_module(MODULE_PATH)
    try:
        assert module.__package__ == f"data.plugins.{PLUGIN_DIR_NAME}"
        # 相对导入生效：qbot 来自复制后的目录，而不是仓库根目录
        imported_from = Path(module.qbot_app.__file__).resolve()
        assert imported_from.is_relative_to(target.resolve())

        plugin = module.Sub2ApiQqBotPlugin(context=object(), config=BASE_CONFIG)
        assert plugin.service.help_text().startswith("📖 Sub2API QQ 助手")
        assert plugin.config.currency_unit == "USD"
    finally:
        for name in list(sys.modules):
            if name == "data" or name.startswith("data.plugins"):
                sys.modules.pop(name, None)


def test_invalid_config_fails_loudly(tmp_path: Path) -> None:
    import main

    with pytest.raises(ValueError, match="sub2api_base_url"):
        main.Sub2ApiQqBotPlugin(
            context=object(),
            config={**BASE_CONFIG, "sub2api_base_url": "ftp://bad"},
        )

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        main.Sub2ApiQqBotPlugin(
            context=object(),
            config={**BASE_CONFIG, "request_timeout_seconds": 999},
        )
