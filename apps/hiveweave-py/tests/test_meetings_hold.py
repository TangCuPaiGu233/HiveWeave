"""团队开会 — hold：park-all（含 ask）、chat 卡口、drain no-op、wait 冻结。

规格 §规格验收 B。mock 的是 LLM/Streamer，不 mock Agent.chat() 卡口。
"""

from __future__ import annotations

import asyncio
import time

from hiveweave.services.meetings import hold as meeting_hold
from hiveweave.services.meetings.orchestrator import start_meeting
from hiveweave.services.meetings.service import meeting_service
from hiveweave.services.wait_contract import wait_contract_service
from tests.meeting_env import (
    get_inbox_rows,
    get_wait_row,
    insert_agent,
    insert_inbox_message,
    insert_wait,
    meeting_env,
)

PID = "mtg-hold-1"


async def test_park_all_has_no_ask_exempt_and_unpark_restores_contract():
    """park_all_pending_wakes **无豁免**；unpark 原样恢复 ask 合同字段。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-1")
        # ask（expect_report=1）与普通消息各一条
        ask = await insert_inbox_message(
            pid, "boss-1", "a-1", "[ASK] please confirm", expect_report=True
        )
        plain = await insert_inbox_message(pid, "peer-1", "a-1", "fyi")
        parked_map = await meeting_hold.apply_hold(pid, "m-1", ["a-1"])
        assert set(parked_map["a-1"]) == {ask["id"], plain["id"]}
        rows = {r["id"]: r for r in await get_inbox_rows(pid, "a-1")}
        assert rows[ask["id"]]["wake"] == 0
        assert rows[ask["id"]]["parked"] == 1
        assert rows[ask["id"]]["read"] == 0
        assert rows[ask["id"]]["expect_report"] == 1  # 合同字段不动
        # 解 hold：恢复原行（parked=0, wake=1, read=0）
        rec = await meeting_hold.release_hold("a-1")
        assert rec is not None
        rows = {r["id"]: r for r in await get_inbox_rows(pid, "a-1")}
        assert rows[ask["id"]]["wake"] == 1
        assert rows[ask["id"]]["parked"] == 0
        assert rows[ask["id"]]["read"] == 0
        assert not meeting_hold.is_held("a-1")


async def test_agent_chat_gate_requeues_and_drain_is_noop():
    """真实 Agent（不 mock chat 卡口）：held 时 chat() 重新入队不启动 LLM，
    _drain_message_queue 完全 no-op（队列保留）。"""
    from hiveweave.agents.agent import Agent

    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-1")
        agent = Agent("a-1", pid, {"name": "A1", "role": "developer"})
        try:
            await meeting_hold.apply_hold(pid, "m-1", ["a-1"])
            res = await agent.chat("hello while held", {"source": "user"})
            assert res.get("ok") is True
            assert res.get("held") is True
            assert len(agent._message_queue) == 1
            assert agent._llm_task is None  # 未启动 LLM
            # drain：held → no-op，队列原样保留
            await agent._drain_message_queue()
            assert len(agent._message_queue) == 1
            # 解 hold 后组织回合闸门重新打开
            assert meeting_hold.may_start_org_turn("a-1") is False
            await meeting_hold.release_hold("a-1")
            assert meeting_hold.may_start_org_turn("a-1") is True
        finally:
            if agent._inbox_watcher_task is not None:
                agent._inbox_watcher_task.cancel()
            if agent._llm_task is not None:
                agent._llm_task.cancel()


async def test_trigger_digest_skips_held_agent():
    """规格三处卡口之三：held 时 _do_trigger 在写 digest 前 return。"""
    from hiveweave.agents import trigger as trigger_mod

    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-1")
        await insert_inbox_message(pid, "peer-1", "a-1", "hello", wake=True)
        await meeting_hold.apply_hold(pid, "m-1", ["a-1"])
        await trigger_mod._do_trigger("a-1", "subordinate")
        # digest 不得写入 chat_messages
        from hiveweave.db import project as project_db

        conn = await project_db.get_project_db_by_project_id(pid)
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE agent_id = ?",
            ["a-1"],
        )
        row = await cur.fetchone()
        await cur.close()
        assert row["c"] == 0
        # inbox 保持未读（未被 ACK；park 在 hold 登记时已把它压成 parked）
        rows = await get_inbox_rows(pid, "a-1")
        assert rows and rows[0]["read"] == 0
        assert rows[0]["parked"] == 1 and rows[0]["wake"] == 0


async def test_wait_ttl_frozen_during_hold_and_extended_after():
    """hold 期间 clear_expired 冻结；解 hold 时 expires_at 补时（不踩踏）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-1")
        # 未到期的 wait（剩余 60s）—— hold 期间不得被 clear，也不得倒计时
        original_exp = int(time.time() * 1000) + 60_000
        wait_id = await insert_wait(pid, "a-1", expires_at=original_exp)
        hold_started = int(time.time() * 1000)
        await meeting_hold.apply_hold(
            pid, "m-1", ["a-1"], started_at_ms=hold_started
        )
        # hold 期间：到期扫描不 clear 该 wait
        cleared = await wait_contract_service.clear_expired(pid)
        assert all(w["id"] != wait_id for w in cleared)
        row = await get_wait_row(pid, wait_id)
        assert row["cleared_at"] is None
        # 解 hold：expires_at += hold 时长（开会时间不计入等待）
        await asyncio.sleep(0.02)
        await meeting_hold.release_hold("a-1")
        row = await get_wait_row(pid, wait_id)
        assert row["expires_at"] >= original_exp + 15
        assert row["cleared_at"] is None
        # 解冻瞬间不批量 WAIT_TIMEOUT（补时后仍远未到期）
        cleared = await wait_contract_service.clear_expired(pid)
        assert all(w["id"] != wait_id for w in cleared)


async def test_interlocking_regression_a_held_b_finishes_no_chat():
    """互等回归（规格 B）：A hold + 塞 ask/dispatch，B 收工后 A 仍未 chat。"""
    from hiveweave.agents.agent import Agent

    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-1")
        a = Agent("a-1", pid, {"name": "A", "role": "developer"})
        try:
            await meeting_hold.apply_hold(pid, "m-1", ["a-1"])
            # 会中塞进来的 ask：即使 wake=1 落进 inbox，held 卡口也保证
            # A 不启动任何 LLM 回合（chat/drain/trigger 三处 no-op）。
            await insert_inbox_message(
                pid, "b-1", "a-1", "[ASK] status?", expect_report=True
            )
            # B（不在名册）收工发完消息 —— A 依然 held、无 LLM 回合
            assert meeting_hold.is_held("a-1")
            assert a._llm_task is None
            await a._drain_message_queue()
            assert a._llm_task is None
        finally:
            if a._inbox_watcher_task is not None:
                a._inbox_watcher_task.cancel()
