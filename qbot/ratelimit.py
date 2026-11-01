"""同一 QQ 的命令冷却。

内存字典 ``{qq: last_ts}``，超过一小时的条目会被清理；``/帮助`` 不受限。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

PRUNE_AFTER_SECONDS = 3600.0


class CommandCooldown:
    """基于单调时钟的命令冷却器。"""

    def __init__(
        self,
        seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        prune_after: float = PRUNE_AFTER_SECONDS,
    ) -> None:
        self._seconds = max(int(seconds), 0)
        self._clock = clock
        self._prune_after = float(prune_after)
        self._last: dict[str, float] = {}

    @property
    def seconds(self) -> int:
        return self._seconds

    def check(self, key: str) -> int:
        """检查并记录。

        Returns:
            0 表示放行；否则为需要等待的剩余秒数（向上取整，至少 1）。
        """
        if self._seconds <= 0:
            return 0
        now = self._clock()
        token = str(key or "")
        last = self._last.get(token)
        if last is not None:
            remain = self._seconds - (now - last)
            if remain > 0:
                return max(math.ceil(remain), 1)
        self._last[token] = now
        self._prune(now)
        return 0

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._last.clear()
        else:
            self._last.pop(str(key), None)

    def _prune(self, now: float) -> None:
        if len(self._last) < 32:
            return
        expired = [
            token
            for token, stamp in self._last.items()
            if now - stamp > self._prune_after
        ]
        for token in expired:
            self._last.pop(token, None)


__all__ = ["CommandCooldown"]
