"""日志适配层。

在 AstrBot 中运行时复用框架的 logger，独立运行（单元测试、脚本）时
回退到标准库 logging，保证本包可以在没有 AstrBot 的环境里被导入。
"""

from __future__ import annotations

import logging

try:  # pragma: no cover - AstrBot 运行时总会成功
    from astrbot.api import logger as logger
except ImportError:  # pragma: no cover - 独立运行时的兜底
    logger = logging.getLogger("astrbot_plugin_sub2api_qqbot")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

__all__ = ["logger"]
