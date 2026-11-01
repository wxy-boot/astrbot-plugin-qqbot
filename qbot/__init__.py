"""Sub2API QQ 助手的纯逻辑实现。

本包不依赖 AstrBot，便于单元测试：配置解析、状态存储、HTTP 客户端、
文案格式化与命令业务逻辑都在这里；``main.py`` 只做 AstrBot 适配。
"""

from __future__ import annotations

__all__ = [
    "app",
    "client",
    "config",
    "formatting",
    "ratelimit",
    "state",
]
