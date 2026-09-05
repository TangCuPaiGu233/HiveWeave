"""件5（42 轮报告 P2-8）：timer wait 的目标时刻语义。

旧行为：commit_turn(waiting_on=[{kind:timer, ref:<目标时刻>}]) 的
expires_at 一律 = created + 15min，agent 设 4h 后目标会被虚假唤醒两次。
新语义（wait_contract.replace_waits）：
  A. 目标 ≤ created+TTL → expires_at = 目标时刻（按目标排队）；
  B. 目标 > created+TTL → expires_at 封顶 TTL，note 打 ttl_cap 标记；
  C. 解析不了目标（quota_reset / alarm-<uuid> 等平台内部 timer）→ 维持旧 TTL。
game_time 唤醒文案区分 wakeup_reason=target_reached | ttl_cap。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

import hiveweave.services.inbox as inbox_mod
import hiveweave.services.wait_contract as wait_mod
from hiveweave.services.game_time import GameTimeService
from hiveweave.services.wait_contract import (
    WaitContractService,
    _conn as _wait_conn,
    default_ttl_ms,
    parse_timer_target_ms,
    wait_target_iso,
    wait_wakeup_reason,
)
from tests.test_idle_architecture_p0 import COORD, EXEC, task_env  # noqa: F401

TTL_MS = default_ttl_ms("timer")  # 15 * 60 * 1000


@pytest.fixture(autouse=True)
def _clear_migrated():
    wait_mod._migrated.clear()
    inbox_mod._migrated.clear()
    yield
    wait_mod._migrated.clear()
    inbox_mod._migrated.clear()


@pytest.fixture(autouse=True)
def _cleanup_game_time_state(task_env):
    yield
    import hiveweave.services.game_time as game_time_mod

    game_time_mod._states.pop(task_env["project_id"], None)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── 解析器单测 ────────────────────────────────────────────


def test_parse_timer_target_ms_formats():
    now = _now_ms()
    # epoch 秒自动升毫秒
    assert parse_timer_target_ms(1770000000) == 1770000000000
    # ISO-8601 Z
    assert parse_timer_target_ms("2030-01-01T15:00:00Z") is not None
    # 纯时刻 HH:MMZ（下一个未来发生点）
    t = parse_timer_target_ms("23:59Z")
    assert t is not None and t > now
    # 平台内部 ref 解析不了 → None
    assert parse_timer_target_ms("quota_reset") is None
    assert parse_timer_target_ms("alarm-1") is None
    assert parse_timer_target_ms(None, "") is None


# ── replace_waits 排队语义 ────────────────────────────────


async def test_timer_target_within_ttl_queues_at_target(task_env):
    """A：目标 ≤ TTL → expires_at = 目标时刻（不是 +900s），打 target_reached。"""
    pid = task_env["project_id"]
    wc = WaitContractService()
    target = _now_ms() + 5 * 60 * 1000  # 5min < 15min TTL
    created = await wc.replace_waits(
        pid, EXEC, [{"kind": "timer", "ref": f"{target}"}], phase="waiting"
    )
    assert len(created) == 1
    w = created[0]
    assert w["expiresAt"] == target  # 按目标排队，而非 created+900s
    assert wait_wakeup_reason(w) == "target_reached"


async def test_timer_target_beyond_ttl_caps_with_ttl_cap(task_env):
    """B：目标 > TTL → expires_at 封顶 TTL，note 打 ttl_cap 标记。"""
    pid = task_env["project_id"]
    wc = WaitContractService()
    before = _now_ms()
    target = before + 4 * 60 * 60 * 1000  # 4h 后 ≫ 15min TTL
    created = await wc.replace_waits(
        pid,
        EXEC,
        [{"kind": "timer", "ref": "2030-01-01T15:00:00Z"}],
        phase="waiting",
    )
    assert len(created) == 1
    w = created[0]
    assert w["expiresAt"] is not None
    # 封顶在 created+TTL 附近（远早于 2030 目标）
    assert before + TTL_MS - 2000 <= w["expiresAt"] <= before + TTL_MS + 5000
    assert wait_wakeup_reason(w) == "ttl_cap"
    assert wait_target_iso(w) is not None
    # 目标行仍在 note 里可追溯
    assert "2030-01-01T15:00:00" in (w["note"] or "")


async def test_timer_target_around_ttl_boundary(task_env):
    """边界：目标 ≈ created+TTL —— 略小于 TTL 走 A，略大于 TTL 走 B。"""
    pid = task_env["project_id"]
    wc = WaitContractService()
    # A 侧：目标 = now + TTL - 5s（< TTL）
    target_a = _now_ms() + TTL_MS - 5000
    created_a = await wc.replace_waits(
        pid, EXEC, [{"kind": "timer", "ref": f"{target_a}"}], phase="waiting"
    )
    assert created_a[0]["expiresAt"] == target_a
    assert wait_wakeup_reason(created_a[0]) == "target_reached"

    # B 侧：目标 = now + TTL + 5s（> TTL，压线不假装到点）
    target_b = _now_ms() + TTL_MS + 5000
    created_b = await wc.replace_waits(
        pid, EXEC, [{"kind": "timer", "ref": f"{target_b}"}], phase="waiting"
    )
    assert created_b[0]["expiresAt"] <= target_b - 4000  # 被封顶提前
    assert wait_wakeup_reason(created_b[0]) == "ttl_cap"


async def test_unparseable_timer_ref_keeps_legacy_ttl(task_env):
    """C：平台内部 timer（quota_reset 等）维持旧 TTL，不打标记。"""
    pid = task_env["project_id"]
    wc = WaitContractService()
    before = _now_ms()
    created = await wc.replace_waits(
        pid, EXEC, [{"kind": "timer", "ref": "quota_reset"}], phase="waiting"
    )
    w = created[0]
    assert before + TTL_MS - 2000 <= w["expiresAt"] <= before + TTL_MS + 5000
    assert wait_wakeup_reason(w) is None


async def test_numeric_note_not_taken_as_past_target(task_env, monkeypatch):
    """P2-1（审计）：ref 解析失败回退 note，note="30" 按 epoch = 1970 ——
    过去时刻不采信：维持旧 TTL 排队，绝不立即假唤醒 + target_reached。"""
    pid = task_env["project_id"]
    svc = GameTimeService(pid)
    wc = WaitContractService()
    before = _now_ms()
    created = await wc.replace_waits(
        pid,
        EXEC,
        [{"kind": "timer", "ref": "quota_reset", "note": "30"}],
        phase="waiting",
    )
    w = created[0]
    assert w["expiresAt"] > before  # 不再是 1970 的立即到期
    assert before + TTL_MS - 2000 <= w["expiresAt"] <= before + TTL_MS + 5000
    assert wait_wakeup_reason(w) is None
    # 且真实唤醒路径不会立刻把它当 target_reached 发出去
    send = AsyncMock()
    trigger = AsyncMock()
    monkeypatch.setattr("hiveweave.services.inbox.InboxService.send_message", send)
    monkeypatch.setattr(GameTimeService, "_watchdog_trigger", trigger)
    handled = await svc.recover_wait_timeouts(pid)
    assert handled["expired_processed"] is False
    send.assert_not_awaited()
    svc.cancel_wait_recovery_timers(pid)


# ── 唤醒文案区分 ──────────────────────────────────────────


async def test_ttl_cap_wake_message_says_target_not_reached(task_env, monkeypatch):
    """ttl_cap 唤醒文案明说「TTL 上限唤醒，目标未到，续等或改 ScheduledAlarm」。"""
    pid = task_env["project_id"]
    svc = GameTimeService(pid)
    wc = WaitContractService()
    created = await wc.replace_waits(
        pid,
        EXEC,
        [{"kind": "timer", "ref": "2030-01-01T15:00:00Z"}],
        phase="waiting",
    )
    assert wait_wakeup_reason(created[0]) == "ttl_cap"
    # 强制到期，走真实唤醒路径
    conn = await _wait_conn(pid)
    cur = await conn.execute(
        "UPDATE agent_waits SET expires_at = ? WHERE id = ?",
        [_now_ms() - 1000, created[0]["id"]],
    )
    await conn.commit()
    await cur.close()

    send = AsyncMock()
    trigger = AsyncMock()
    monkeypatch.setattr("hiveweave.services.inbox.InboxService.send_message", send)
    monkeypatch.setattr(GameTimeService, "_watchdog_trigger", trigger)

    await svc.recover_wait_timeouts(pid)

    send.assert_awaited_once()
    body = send.await_args.kwargs["message"]
    assert "wakeup_reason=ttl_cap" in body
    assert "2030-01-01T15:00:00" in body  # 目标时刻明示
    assert "commit_turn(waiting_on)" in body
    assert "schedule_alarm" in body


async def test_target_reached_wake_message_labeled(task_env, monkeypatch):
    """target_reached 唤醒文案带 wakeup_reason=target_reached 标注。"""
    pid = task_env["project_id"]
    svc = GameTimeService(pid)
    wc = WaitContractService()
    target = _now_ms() + 60_000
    created = await wc.replace_waits(
        pid, EXEC, [{"kind": "timer", "ref": f"{target}"}], phase="waiting"
    )
    assert wait_wakeup_reason(created[0]) == "target_reached"
    conn = await _wait_conn(pid)
    cur = await conn.execute(
        "UPDATE agent_waits SET expires_at = ? WHERE id = ?",
        [_now_ms() - 1000, created[0]["id"]],
    )
    await conn.commit()
    await cur.close()

    send = AsyncMock()
    trigger = AsyncMock()
    monkeypatch.setattr("hiveweave.services.inbox.InboxService.send_message", send)
    monkeypatch.setattr(GameTimeService, "_watchdog_trigger", trigger)

    await svc.recover_wait_timeouts(pid)

    send.assert_awaited_once()
    body = send.await_args.kwargs["message"]
    assert "wakeup_reason=target_reached" in body
    assert "ttl_cap" not in body
