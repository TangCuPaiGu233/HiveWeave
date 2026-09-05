"""会议 hold — park-all（含 ask）+ 组织回合闸门。

规格阻断项：**禁止复用** ``services/inbox.py`` 的 ``park_pending_wakes`` ——
它豁免 ask/协议前缀（互等死锁源）。本模块的 ``park_all_pending_wakes``
**无任何豁免**：wake=1 未读一律压成 wake=0 + parked=1，解 hold 时原样
恢复（``parked=0, wake=1, read=0``，不改 expect_report / reply 合同字段）。

wait 合同冻结：
- hold 期间 ``wait_contract`` 的 clear_expired / backfill_null_expires
  排除 held agents（单一卡点在本模块 ``held_agent_ids``，fail-open）；
- 解 hold 时 ``expires_at += (now - hold_started_at)``（开会时间不计入
  等待，避免解冻瞬间 WAIT_TIMEOUT 踩踏）。

hold 注册表是进程内存态（进程重启由 ``recover_meetings`` 泵按 meetings 行
重建 hold，hold_started_at 取自 meetings 表）。
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from hiveweave.db import project as project_db
from hiveweave.services.inbox import InboxService, _ensure_schema

log = structlog.get_logger(__name__)

inbox_service = InboxService()

# agent_id -> hold record
_HOLDS: dict[str, dict[str, Any]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── 查询（卡口用，禁止再散落更多消费点）──────────────────────


def is_held(agent_id: str) -> bool:
    """三处卡口的唯一判定：Agent.chat / _drain_message_queue / _do_trigger。"""
    return str(agent_id or "") in _HOLDS


def hold_record(agent_id: str) -> dict[str, Any] | None:
    return _HOLDS.get(str(agent_id or ""))


def held_agent_ids(project_id: str | None = None) -> set[str]:
    """当前被 hold 的 agent ids（可按项目过滤；wait_contract 冻结消费）。"""
    if project_id is None:
        return set(_HOLDS.keys())
    return {
        aid
        for aid, rec in _HOLDS.items()
        if str(rec.get("project_id") or "") == str(project_id)
    }


def may_start_org_turn(agent_id: str) -> bool:
    """组织回合（非会务）是否可启动 —— is_held 的正向别名。"""
    return not is_held(agent_id)


def reset_holds_for_tests() -> None:
    _HOLDS.clear()


# ── park / unpark（无豁免版）─────────────────────────────────


async def park_all_pending_wakes(agent_id: str) -> list[str]:
    """把该 agent 全部 wake=1 未读压成 wake=0 + parked=1（**无豁免**）。

    与 ``inbox.park_pending_wakes`` 的区别：不豁免 ask / expect_report /
    协议前缀 —— 会议是短暂冻结，ask 合同必须原样保留到散会。
    返回本次 park 的 id + **已处 parked 态的未读行** id（崩溃恢复：泵重建
    hold 时接管上次 park 的行，release 才能把它们一并解回）。
    """
    await _ensure_schema(agent_id)
    rows = await project_db.query(
        agent_id,
        "SELECT id FROM inbox WHERE to_agent_id = ? AND read = 0 "
        "AND COALESCE(wake, 1) = 1 AND COALESCE(parked, 0) = 0",
        [agent_id],
    )
    ids = [str(r["id"]) for r in rows or [] if r["id"]]
    if ids:
        placeholders = ", ".join("?" for _ in ids)
        await project_db.execute(
            agent_id,
            f"UPDATE inbox SET wake = 0, parked = 1 "
            f"WHERE to_agent_id = ? AND id IN ({placeholders})",
            [agent_id, *ids],
        )
        log.info("meeting_park_all", agent_id=agent_id, parked=len(ids))
    # 接管已有 parked 行（上次 hold 的残留 —— 进程重启后注册表丢失）
    adopted = await project_db.query(
        agent_id,
        "SELECT id FROM inbox WHERE to_agent_id = ? AND read = 0 "
        "AND COALESCE(parked, 0) = 1",
        [agent_id],
    )
    all_ids = list(ids)
    for r in adopted or []:
        rid = str(r["id"])
        if rid not in all_ids:
            all_ids.append(rid)
    return all_ids


async def unpark_original(agent_id: str, message_ids: list[str]) -> int:
    """恢复 park_all 掉的原 inbox 行：parked=0, wake=1, read=0（不改合同）。"""
    ids = [m for m in (message_ids or []) if m]
    if not ids:
        return 0
    await _ensure_schema(agent_id)
    placeholders = ", ".join("?" for _ in ids)
    try:
        await project_db.execute(
            agent_id,
            f"UPDATE inbox SET parked = 0, wake = 1, read = 0 "
            f"WHERE to_agent_id = ? AND id IN ({placeholders})",
            [agent_id, *ids],
        )
    except Exception as e:
        log.warning("meeting_unpark_failed", agent_id=agent_id, error=str(e))
        return 0
    return len(ids)


# ── hold 生命周期 ────────────────────────────────────────────


async def _cancel_org_timers(agent_id: str) -> None:
    """取消 productive_continue / interrupted_resume 定时器（规格 hold #3）。"""
    try:
        from hiveweave.agents.supervisor import agent_manager

        agent = agent_manager.get_agent(agent_id)
        if agent is None:
            return
        agent._cancel_productive_continue()
        agent._cancel_interrupted_resume()
    except Exception as e:
        log.debug("meeting_hold_timer_cancel_failed",
                  agent_id=agent_id, error=str(e))


async def apply_hold(
    project_id: str,
    meeting_id: str,
    agent_ids: list[str],
    *,
    started_at_ms: int | None = None,
) -> dict[str, list[str]]:
    """登记 hold：park-all + 取消续跑定时器。幂等（重复 apply 只刷新）。"""
    parked_map: dict[str, list[str]] = {}
    started = int(started_at_ms) if started_at_ms else _now_ms()
    for aid in agent_ids or []:
        aid = str(aid or "").strip()
        if not aid:
            continue
        existing = _HOLDS.get(aid)
        if existing and existing.get("meeting_id") == meeting_id:
            parked_map[aid] = list(existing.get("parked_ids") or [])
            continue
        rec: dict[str, Any] = {
            "meeting_id": meeting_id,
            "project_id": project_id,
            "started_at_ms": started,
            "parked_ids": [],
        }
        _HOLDS[aid] = rec
        try:
            rec["parked_ids"] = await park_all_pending_wakes(aid)
        except Exception as e:
            log.warning("meeting_hold_park_failed", agent_id=aid, error=str(e))
        await _cancel_org_timers(aid)
        parked_map[aid] = list(rec["parked_ids"])
    log.info(
        "meeting_hold_applied",
        project_id=project_id,
        meeting_id=meeting_id[:12],
        agents=len(parked_map),
    )
    return parked_map


async def release_hold(agent_id: str) -> dict[str, Any] | None:
    """解除单人 hold：unpark 原行 + wait 补时 + 清登记。

    返回被释放的 hold record（无 hold 时 None）。调用方负责随后的
    一次普通 trigger 回岗唤醒。
    """
    rec = _HOLDS.pop(str(agent_id or ""), None)
    if rec is None:
        return None
    try:
        n = await unpark_original(agent_id, list(rec.get("parked_ids") or []))
        if n:
            log.info("meeting_unparked", agent_id=agent_id, count=n)
    except Exception as e:
        log.warning("meeting_release_unpark_failed",
                    agent_id=agent_id, error=str(e))
    await extend_frozen_waits(
        str(rec.get("project_id") or ""),
        agent_id,
        hold_ms=max(0, _now_ms() - int(rec.get("started_at_ms") or _now_ms())),
    )
    return rec


def clear_hold_silent(agent_id: str) -> None:
    """仅清登记（dismiss/off-duty 等不再回岗的路径）。"""
    _HOLDS.pop(str(agent_id or ""), None)


async def extend_frozen_waits(
    project_id: str, agent_id: str, hold_ms: int
) -> int:
    """wait 钟补时：``expires_at += hold_ms``（active waits only）。"""
    if hold_ms <= 0 or not project_id or not agent_id:
        return 0
    try:
        await project_db.execute_by_project(
            project_id,
            "UPDATE agent_waits SET expires_at = COALESCE(expires_at, 0) + ? "
            "WHERE agent_id = ? AND cleared_at IS NULL "
            "AND expires_at IS NOT NULL",
            [int(hold_ms), agent_id],
        )
        return 1
    except Exception as e:
        log.warning("meeting_wait_extend_failed",
                    project_id=project_id, agent_id=agent_id, error=str(e))
        return 0


async def refresh_last_active(project_id: str, agent_ids: list[str]) -> None:
    """回岗后 ``last_active_at = now``，避免沉默看门狗立刻红框。"""
    now = _now_ms()
    for aid in agent_ids or []:
        try:
            await project_db.execute_by_project(
                project_id,
                "UPDATE agents SET last_active_at = ? WHERE id = ?",
                [now, aid],
            )
        except Exception as e:
            log.debug("meeting_last_active_refresh_failed",
                      agent_id=aid, error=str(e))
