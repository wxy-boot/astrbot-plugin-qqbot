"""``state.json`` 读写、原子写入与损坏保护测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from qbot.state import (
    SUPPORTED_VERSION,
    Binding,
    BindingStore,
    StateReadOnlyError,
)


def make_store(tmp_path: Path) -> BindingStore:
    return BindingStore(tmp_path / "state.json")


def read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_load_missing_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    assert await store.snapshot() == {}
    assert store.read_only is False
    assert not (tmp_path / "state.json").exists()


async def test_bind_writes_versioned_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await store.bind("123456789", Binding(uid="42", email="alice@example.com", name="alice", bound_at=1762000000))

    data = read_state(tmp_path / "state.json")
    assert data["version"] == SUPPORTED_VERSION
    assert data["bindings"]["123456789"]["uid"] == "42"
    assert data["bindings"]["123456789"]["email"] == "alice@example.com"
    assert data["bindings"]["123456789"]["bound_at"] == 1762000000
    # 不保存余额、签到/抽奖记录
    assert set(data["bindings"]["123456789"]) == {"uid", "email", "name", "bound_at"}
    # 临时文件不残留
    assert not (tmp_path / "state.json.tmp").exists()


async def test_reload_restores_bindings(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await store.bind("123", Binding(uid="42", email="alice@example.com"))

    reloaded = make_store(tmp_path)
    await reloaded.load()
    binding = await reloaded.get("123")
    assert binding is not None
    assert binding.uid == "42"


async def test_uid_is_stored_as_string(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await store.bind("123", Binding(uid=42, email="alice@example.com"))  # type: ignore[arg-type]
    assert read_state(tmp_path / "state.json")["bindings"]["123"]["uid"] == "42"


async def test_find_qq_by_uid(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await store.bind("111", Binding(uid="42", email="a@example.com"))
    assert await store.find_qq("42") == "111"
    assert await store.find_qq(42) == "111"  # type: ignore[arg-type]
    assert await store.find_qq("99") is None


async def test_remove_binding(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await store.bind("111", Binding(uid="42", email="a@example.com"))
    removed = await store.remove("111")
    assert removed is not None and removed.uid == "42"
    assert await store.get("111") is None
    assert read_state(tmp_path / "state.json")["bindings"] == {}
    assert await store.remove("111") is None


async def test_corrupt_file_moves_to_backup_and_read_only(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = make_store(tmp_path)
    await store.load()

    assert store.read_only is True
    assert store.read_only_reason == "corrupt"
    assert await store.get("111") is None
    assert not path.exists(), "损坏文件应被移走，且不自动重建"
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert "this is not json" in backups[0].read_text(encoding="utf-8")

    with pytest.raises(StateReadOnlyError):
        await store.bind("111", Binding(uid="42", email="a@example.com"))


async def test_non_object_root_is_corrupt(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
    store = make_store(tmp_path)
    await store.load()
    assert store.read_only_reason == "corrupt"


async def test_newer_version_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"version": SUPPORTED_VERSION + 1, "bindings": {}}),
        encoding="utf-8",
    )
    store = make_store(tmp_path)
    await store.load()
    assert store.read_only is True
    assert store.read_only_reason == "version"
    assert path.exists(), "版本不兼容时不应移动文件"

    with pytest.raises(StateReadOnlyError):
        await store.remove("111")


async def test_version_zero_is_treated_as_v1(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(
        json.dumps({"version": 0, "bindings": {"111": {"uid": "42", "email": "a@example.com"}}}),
        encoding="utf-8",
    )
    store = make_store(tmp_path)
    await store.load()
    assert store.read_only is False
    binding = await store.get("111")
    assert binding is not None and binding.uid == "42"


async def test_missing_version_is_treated_as_v1(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(
        json.dumps({"bindings": {"111": {"uid": "42", "email": "a@example.com"}}}),
        encoding="utf-8",
    )
    store = make_store(tmp_path)
    await store.load()
    assert store.read_only is False
    assert await store.get("111") is not None


async def test_invalid_binding_entries_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    "111": {"uid": "42", "email": "a@example.com"},
                    "222": {"email": "b@example.com"},
                    "333": "not-an-object",
                },
            }
        ),
        encoding="utf-8",
    )
    store = make_store(tmp_path)
    await store.load()
    assert set((await store.snapshot()).keys()) == {"111"}


async def test_concurrent_writes_are_serialized(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await asyncio.gather(
        *[
            store.bind(str(1000 + index), Binding(uid=str(index), email=f"u{index}@example.com"))
            for index in range(20)
        ]
    )
    snapshot = await store.snapshot()
    assert len(snapshot) == 20
    data = read_state(tmp_path / "state.json")
    assert len(data["bindings"]) == 20
    assert data["version"] == SUPPORTED_VERSION


async def test_rebinding_same_qq_overwrites(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.load()
    await store.bind("111", Binding(uid="42", email="a@example.com"))
    await store.bind("111", Binding(uid="43", email="c@example.com"))
    binding = await store.get("111")
    assert binding is not None and binding.uid == "43"
    assert len(await store.snapshot()) == 1


async def test_empty_file_is_tolerated(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("   ", encoding="utf-8")
    store = make_store(tmp_path)
    await store.load()
    assert store.read_only is False
    assert await store.snapshot() == {}


def test_binding_from_dict_tolerates_bad_values() -> None:
    binding = Binding.from_dict({"uid": 42, "email": None, "name": 3, "bound_at": "oops"})
    assert binding is not None
    assert binding.uid == "42"
    assert binding.email == ""
    assert binding.name == "3"
    assert binding.bound_at == 0


def test_binding_from_dict_requires_uid() -> None:
    assert Binding.from_dict({"email": "a@example.com"}) is None
    assert Binding.from_dict("nope") is None
