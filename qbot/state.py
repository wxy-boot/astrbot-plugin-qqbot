"""绑定关系持久化（``state.json``）。

设计要点：

- 只保存 QQ → Sub2API 账号的映射，不保存余额、签到/抽奖记录；
- 所有修改在 ``asyncio.Lock`` 内完成，写入使用「临时文件 + fsync + 原子替换」；
- 文件损坏时进入只读保护状态，并把原文件重命名为 ``state.json.corrupt-<ts>``，
  不自动重建空文件，避免悄悄丢失全部绑定。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .log import logger

STATE_FILENAME = "state.json"
SUPPORTED_VERSION = 1


class StateError(Exception):
    """状态存储相关异常基类。"""


class StateCorruptError(StateError):
    """state.json 内容损坏。"""


class StateReadOnlyError(StateError):
    """存储进入只读保护状态，拒绝写入。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Binding:
    """一条 QQ 与 Sub2API 账号的绑定关系。"""

    uid: str
    email: str
    name: str = ""
    bound_at: int = 0

    def __post_init__(self) -> None:
        # uid 统一存字符串，避免 Sub2API 数字/UUID 类型差异
        object.__setattr__(self, "uid", str(self.uid).strip())
        object.__setattr__(self, "email", str(self.email or "").strip())
        object.__setattr__(self, "name", str(self.name or "").strip())
        try:
            object.__setattr__(self, "bound_at", int(self.bound_at or 0))
        except (TypeError, ValueError):
            object.__setattr__(self, "bound_at", 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "email": self.email,
            "name": self.name,
            "bound_at": self.bound_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Binding | None":
        if not isinstance(data, dict):
            return None
        uid = str(data.get("uid") or "").strip()
        if not uid:
            return None
        try:
            bound_at = int(data.get("bound_at") or 0)
        except (TypeError, ValueError):
            bound_at = 0
        return cls(
            uid=uid,
            email=str(data.get("email") or "").strip(),
            name=str(data.get("name") or "").strip(),
            bound_at=bound_at,
        )


class BindingStore:
    """``state.json`` 的异步读写封装。"""

    def __init__(self, path: str | os.PathLike[str]):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._bindings: dict[str, Binding] = {}
        self._read_only_reason: str | None = None
        self._loaded = False

    # ---------------------------------------------------------------- 只读属性

    @property
    def path(self) -> Path:
        return self._path

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def read_only_reason(self) -> str | None:
        return self._read_only_reason

    @property
    def read_only(self) -> bool:
        return self._read_only_reason is not None

    # ---------------------------------------------------------------- 载入文件

    async def load(self) -> None:
        """从磁盘载入绑定关系；损坏时进入只读保护状态。"""
        async with self._lock:
            self._loaded = True
            try:
                self._load_locked()
            except StateCorruptError as exc:
                self._enter_read_only(exc)

    def _load_locked(self) -> None:
        self._bindings = {}
        self._read_only_reason = None
        if not self._path.exists():
            logger.info("未发现 %s，将在首次绑定时创建", self._path.name)
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateCorruptError(f"读取失败：{exc}") from exc
        if not raw.strip():
            logger.warning("%s 内容为空，按空绑定处理", self._path.name)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateCorruptError(f"JSON 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise StateCorruptError("根节点不是 JSON 对象")

        version = data.get("version", 0)
        try:
            version_number = int(version)
        except (TypeError, ValueError) as exc:
            raise StateCorruptError(f"version 字段非法：{version!r}") from exc
        if version_number > SUPPORTED_VERSION:
            self._read_only_reason = "version"
            logger.error(
                "%s 的 version=%s 高于插件支持的 %s，请升级插件",
                self._path.name,
                version_number,
                SUPPORTED_VERSION,
            )
            return

        bindings = data.get("bindings", {})
        if not isinstance(bindings, dict):
            raise StateCorruptError("bindings 字段不是 JSON 对象")
        parsed: dict[str, Binding] = {}
        skipped = 0
        for qq, item in bindings.items():
            binding = Binding.from_dict(item)
            if binding is None:
                skipped += 1
                continue
            parsed[str(qq).strip()] = binding
        if skipped:
            logger.warning("有 %s 条绑定记录格式不正确，已跳过", skipped)
        self._bindings = parsed
        logger.info("已载入 %s 条绑定记录", len(parsed))

    def _enter_read_only(self, exc: StateCorruptError) -> None:
        self._read_only_reason = "corrupt"
        self._bindings = {}
        logger.error("%s 损坏：%s", self._path.name, exc)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        try:
            os.replace(self._path, target)
            logger.error(
                "已将损坏的 %s 备份为 %s；插件进入只读保护状态",
                self._path.name,
                target.name,
            )
        except OSError:
            logger.exception("备份损坏的 %s 失败", self._path.name)

    # ---------------------------------------------------------------- 读取接口

    async def get(self, qq: str | int) -> Binding | None:
        async with self._lock:
            return self._bindings.get(str(qq).strip())

    async def snapshot(self) -> dict[str, Binding]:
        async with self._lock:
            return dict(self._bindings)

    async def find_qq(self, uid: str | int) -> str | None:
        """返回已绑定该 uid 的 QQ 号。"""
        target = str(uid).strip()
        async with self._lock:
            for qq, binding in self._bindings.items():
                if binding.uid == target:
                    return qq
        return None

    def __len__(self) -> int:
        return len(self._bindings)

    # ---------------------------------------------------------------- 写入接口

    async def bind(self, qq: str | int, binding: Binding) -> None:
        key = str(qq).strip()
        if not key:
            raise ValueError("QQ 号不能为空")
        async with self._lock:
            self._ensure_writable_locked()
            self._bindings[key] = binding
            self._write_locked()

    async def remove(self, qq: str | int) -> Binding | None:
        key = str(qq).strip()
        async with self._lock:
            self._ensure_writable_locked()
            binding = self._bindings.pop(key, None)
            if binding is not None:
                self._write_locked()
            return binding

    def _ensure_writable_locked(self) -> None:
        if self._read_only_reason is not None:
            raise StateReadOnlyError(self._read_only_reason)

    def _write_locked(self) -> None:
        payload = {
            "version": SUPPORTED_VERSION,
            "bindings": {
                qq: binding.to_dict() for qq, binding in sorted(self._bindings.items())
            },
        }
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
        if os.name != "nt":  # POSIX 上尽量收紧权限
            with contextlib.suppress(OSError):
                os.chmod(self._path, 0o600)


__all__ = [
    "Binding",
    "BindingStore",
    "STATE_FILENAME",
    "SUPPORTED_VERSION",
    "StateCorruptError",
    "StateError",
    "StateReadOnlyError",
]
