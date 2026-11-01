"""命令冷却测试。"""

from __future__ import annotations

from qbot.ratelimit import CommandCooldown


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_first_call_is_allowed() -> None:
    clock = FakeClock()
    cooldown = CommandCooldown(2, clock=clock)
    assert cooldown.check("111") == 0


def test_second_call_within_window_is_blocked() -> None:
    clock = FakeClock()
    cooldown = CommandCooldown(2, clock=clock)
    assert cooldown.check("111") == 0
    clock.advance(0.5)
    assert cooldown.check("111") == 2  # 剩余 1.5 秒，向上取整
    clock.advance(1.0)
    assert cooldown.check("111") == 1
    clock.advance(0.6)
    assert cooldown.check("111") == 0


def test_cooldown_is_per_key() -> None:
    clock = FakeClock()
    cooldown = CommandCooldown(5, clock=clock)
    assert cooldown.check("111") == 0
    assert cooldown.check("222") == 0
    assert cooldown.check("111") > 0


def test_zero_seconds_disables_cooldown() -> None:
    clock = FakeClock()
    cooldown = CommandCooldown(0, clock=clock)
    assert cooldown.check("111") == 0
    assert cooldown.check("111") == 0


def test_reset_clears_state() -> None:
    clock = FakeClock()
    cooldown = CommandCooldown(10, clock=clock)
    cooldown.check("111")
    cooldown.reset("111")
    assert cooldown.check("111") == 0

    cooldown.check("222")
    cooldown.reset()
    assert cooldown.check("222") == 0


def test_old_entries_are_pruned() -> None:
    clock = FakeClock()
    cooldown = CommandCooldown(1, clock=clock, prune_after=60)
    for index in range(40):
        cooldown.check(str(index))

    clock.advance(120)
    for index in range(40, 80):
        cooldown.check(str(index))
    # 过期条目已被清理，字典不会无限增长
    assert len(cooldown._last) <= 40
