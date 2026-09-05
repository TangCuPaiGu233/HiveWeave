"""团队开会 — 卡住、解散、泵恢复（docs/spec/team-meeting.md §卡住/审计补测）。"""

from __future__ import annotations

from hiveweave.db import project as project_db
from hiveweave.services.meetings import hold as meeting_hold
from hiveweave.services.meetings import orchestrator, prompts
from hiveweave.services.meetings.service import meeting_service
from tests.meeting_env import (
    get_inbox_rows,
    insert_agent,
    meeting_env,
)

PID = "mtg-abort-1"


async def test_chair_timeout_rewake_then_abort_releases_lock():
    """主持 180s 无 continue/conclude → 重唤一次 → abort → hold 释放 →
    可开下一场（审计补测）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        chair_calls = {"n": 0}

        async def fake_runner(agent, *, tool_profile, briefing, allow_continue, timeout_s):
            if tool_profile == "participant":
                return {"action": "abstain", "content": "busy"}
            chair_calls["n"] += 1
            return {"action": "none", "content": "no decision"}

        orchestrator.set_runner_fn(fake_runner)
        m = await orchestrator.start_meeting(
            pid, "chair-1", "stuck", ["topic-1"], ["p-1"]
        )
        await orchestrator.running_task(m["id"])
        assert chair_calls["n"] == 2  # 首跑 + 重唤一次
        final = await meeting_service.get_meeting(pid, m["id"])
        assert final["status"] == "aborted"
        # 无结论（平台不编造定论）
        assert final["topic_results"] == []
        # [MEETING ABORTED]（结构化原因 chair_timeout），无 RESULT
        rows = await get_inbox_rows(pid, "p-1")
        assert any(
            (r["message"] or "").startswith(prompts.MEETING_ABORTED_TAG)
            and "chair_timeout" in (r["message"] or "")
            for r in rows
        )
        assert not any(
            (r["message"] or "").startswith(prompts.MEETING_RESULT_TAG)
            for r in rows
        )
        assert not meeting_hold.is_held("p-1")
        assert not meeting_hold.is_held("chair-1")
        # 锁已释放：可开下一场
        m2 = await meeting_service.create_meeting(
            pid, "chair-1", "next", ["topic-1"], ["p-1"]
        )
        assert m2["status"] == "assembling"


async def test_chair_dismissed_aborts_and_clears_hold():
    """主席 dismiss → aborted(chair_dismissed)、hold 清空（审计补测）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        # 直接构造进行中状态（编排器未启动 —— dismiss 走独立钩子路径）
        m = await meeting_service.create_meeting(
            pid, "chair-1", "t", ["topic-1"], ["p-1"]
        )
        await meeting_hold.apply_hold(
            pid, m["id"], list(m["participants"]),
            started_at_ms=m["hold_started_at"],
        )
        assert meeting_hold.is_held("chair-1")
        assert meeting_hold.is_held("p-1")
        await orchestrator.handle_agent_dismissed(pid, "chair-1")
        final = await meeting_service.get_meeting(pid, m["id"])
        assert final["status"] == "aborted"
        rows = await get_inbox_rows(pid, "p-1")
        assert any("chair_dismissed" in (r["message"] or "") for r in rows)
        assert not meeting_hold.is_held("chair-1")
        assert not meeting_hold.is_held("p-1")


async def test_non_chair_dismiss_roster_lt2_aborts():
    """非主席全 dismiss → 弃权 + 移出名册；活人 <2 → abort（审计补测）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        m = await meeting_service.create_meeting(
            pid, "chair-1", "t", ["topic-1"], ["p-1"]
        )
        await meeting_hold.apply_hold(
            pid, m["id"], list(m["participants"]),
            started_at_ms=m["hold_started_at"],
        )
        # p-1 已被归档（dismiss 主流程完成后的行状态）
        await project_db.execute_by_project(
            pid, "UPDATE agents SET status = 'archived' WHERE id = 'p-1'"
        )
        await orchestrator.handle_agent_dismissed(pid, "p-1")
        final = await meeting_service.get_meeting(pid, m["id"])
        assert final["status"] == "aborted"
        # p-1 已移出名册 + 记弃权
        assert "p-1" not in (final["participants"] or [])
        utters = await meeting_service.get_utterances(
            pid, m["id"], topic_index=0, round_index=0, roles=("abstain",)
        )
        assert any(u["agent_id"] == "p-1" for u in utters)
        rows = await get_inbox_rows(pid, "chair-1")
        assert any("roster_lt2" in (r["message"] or "") for r in rows)


async def test_recover_meetings_resumes_collecting_with_abstains():
    """recover_meetings：collecting 中途恢复 → 未发言者 abstain、主持被叫。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1", "p-2"):
            await insert_agent(pid, aid)
        m = await meeting_service.create_meeting(
            pid, "chair-1", "crash", ["topic-1"], ["p-1", "p-2"]
        )
        await meeting_service.set_status(pid, m["id"], "collecting")
        await meeting_service._update(pid, m["id"], round_index=1)
        # 只有 p-1 来得及发言就崩溃了
        await meeting_service.record_utterance(
            pid, m["id"], topic_index=0, round_index=1,
            agent_id="p-1", role="speech", content="half-way speech",
        )
        # hold 全部丢失（进程重启模拟）
        meeting_hold.clear_hold_silent("chair-1")
        meeting_hold.clear_hold_silent("p-1")
        meeting_hold.clear_hold_silent("p-2")

        async def fake_runner(agent, *, tool_profile, briefing, allow_continue, timeout_s):
            if tool_profile == "participant":
                return {"action": "abstain", "content": "should-not-be-called"}
            return {"action": "conclude", "result": "recovered conclusion"}

        orchestrator.set_runner_fn(fake_runner)
        stats = await orchestrator.recover_meetings(pid)
        assert stats["resumed"] == 1
        # 等恢复任务跑完（泵 create_task 后不阻塞；测试要确定性收尾）
        task = orchestrator.running_task(m["id"])
        assert task is not None
        await task
        final = await meeting_service.get_meeting(pid, m["id"])
        assert final["status"] == "concluded"
        assert final["topic_results"][0]["result"] == "recovered conclusion"
        # 未发言者补弃权（p-2）；恢复后 hold 已随散会清空
        utters = await meeting_service.get_utterances(
            pid, m["id"], topic_index=0, round_index=1, roles=("abstain",)
        )
        assert any(u["agent_id"] == "p-2" for u in utters)
        assert not meeting_hold.is_held("p-1")


async def test_off_duty_aborts_without_zombie_rows(monkeypatch):
    """下班中途 → aborted(off_duty)，无僵尸进行中行（审计补测）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        from hiveweave.services import project_lifecycle

        async def fake_known_off_duty(p):
            return True

        monkeypatch.setattr(
            project_lifecycle, "project_known_off_duty", fake_known_off_duty
        )
        m = await meeting_service.create_meeting(
            pid, "chair-1", "t", ["topic-1"], ["p-1"]
        )
        await meeting_hold.apply_hold(
            pid, m["id"], list(m["participants"]),
            started_at_ms=m["hold_started_at"],
        )
        stats = await orchestrator.recover_meetings(pid)
        assert stats["aborted"] == 1
        final = await meeting_service.get_meeting(pid, m["id"])
        assert final["status"] == "aborted"
        assert not meeting_hold.is_held("p-1")
        rows = await get_inbox_rows(pid, "p-1")
        assert any("off_duty" in (r["message"] or "") for r in rows)
        # 无僵尸进行中行
        assert await meeting_service.get_active_meeting(pid) is None
