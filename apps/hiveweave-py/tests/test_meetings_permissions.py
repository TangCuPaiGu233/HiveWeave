"""团队开会 — 发起与权限（docs/spec/team-meeting.md §规格验收 A）。"""

from __future__ import annotations

import asyncio

import pytest

from hiveweave.services.meetings import hold as meeting_hold
from hiveweave.services.meetings.service import (
    MeetingConflict,
    meeting_service,
)
from hiveweave.services.policy import tool_hard_deny
from tests.meeting_env import (
    agent_row,
    insert_agent,
    meeting_env,
)

PID = "mtg-perm-1"


# ── family 硬门（不是「未映射即放行」）─────────────────────────


def test_start_team_meeting_family_hard_gate():
    # executor / qa / 未知 family 被硬拒，提示写明谁能开
    for row in (
        agent_row("developer", "executor"),
        agent_row("qa_engineer", "coordinator"),  # family=qa（perm 不抬 family）
        agent_row("hr"),
    ):
        reason = tool_hard_deny(row, "start_team_meeting")
        assert reason is not None, row
        assert "ceo" in reason and "coordinator" in reason, reason
    # ceo / coordinator 放行
    assert tool_hard_deny(agent_row("ceo", "readonly"), "start_team_meeting") is None
    assert (
        tool_hard_deny(agent_row("mid", "coordinator"), "start_team_meeting")
        is None
    )


def test_meeting_runner_tools_hard_denied_and_hidden_from_daily_tables():
    from hiveweave.services.permission import (
        CEO_TOOLS,
        COORDINATOR_BUILDER_TOOLS,
        EXECUTOR_BASE_TOOLS,
        HR_TOOLS,
    )

    for fam in (
        agent_row("ceo", "readonly"),
        agent_row("mid", "coordinator"),
        agent_row("developer", "executor"),
        agent_row("hr"),
        agent_row("qa_engineer", "readwrite"),
    ):
        for name in (
            "speak_in_meeting",
            "continue_meeting_round",
            "conclude_topic",
        ):
            assert tool_hard_deny(fam, name) is not None, (fam, name)
    # 日常表看不见 speak/continue/conclude
    daily = (
        set(CEO_TOOLS)
        | set(COORDINATOR_BUILDER_TOOLS)
        | set(EXECUTOR_BASE_TOOLS)
        | set(HR_TOOLS)
    )
    for name in (
        "speak_in_meeting",
        "continue_meeting_round",
        "conclude_topic",
    ):
        assert name not in daily
    # start 进 CEO 与 coordinator 两张表
    assert "start_team_meeting" in CEO_TOOLS
    assert "start_team_meeting" in COORDINATOR_BUILDER_TOOLS
    assert "start_team_meeting" not in EXECUTOR_BASE_TOOLS
    assert "start_team_meeting" not in HR_TOOLS


async def test_permission_service_denies_executor_via_hard_gate(monkeypatch):
    """权限管线层：executor 调 start_team_meeting 在 evaluate 阶段被硬拒。"""
    from hiveweave.db import meta as meta_db
    from hiveweave.services.permission import permission_service

    row = agent_row("developer", "executor")

    async def fake_get_agent(agent_id):
        return row

    monkeypatch.setattr(meta_db, "get_agent_by_id", fake_get_agent)
    decision, reason = await permission_service.evaluate_detailed(
        "exec-x", "start_team_meeting", {}
    )
    assert decision == "deny"
    assert reason is not None and "ceo" in reason


# ── 同项目唯一约束（DB 约束，非 check-then-insert）────────────


async def test_concurrent_start_only_one_active_row():
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await meeting_service.create_meeting(
            pid, "ceo-1", "t", ["topic-1"], ["a-1", "a-2"]
        )
        with pytest.raises(MeetingConflict):
            await meeting_service.create_meeting(
                pid, "ceo-1", "t2", ["topic-1"], ["a-1", "a-2"]
            )
        # abort 后锁释放，可以开下一场
        active = await meeting_service.get_active_meeting(pid)
        await meeting_service.set_status(pid, active["id"], "aborted")
        m2 = await meeting_service.create_meeting(
            pid, "ceo-1", "t3", ["topic-1"], ["a-1", "a-2"]
        )
        assert m2["status"] == "assembling"


# ── 工具：立即返回 + hold-before-fanout + roster 校验 ──────────


async def test_start_tool_validates_and_registers_hold_immediately():
    from hiveweave.services.meetings import orchestrator
    from hiveweave.tools.meeting_tools import StartTeamMeetingParams
    from hiveweave.tools.meeting_tools import start_team_meeting_tool

    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "ceo-1", role="ceo", permission_type="readonly")
        await insert_agent(pid, "dev-1", role="developer")
        await insert_agent(
            pid, "dev-gone", role="developer", status="archived"
        )
        # archived 拒绝
        res = await start_team_meeting_tool(
            StartTeamMeetingParams(
                title="t",
                topics=["q1"],
                participant_ids=["dev-1", "dev-gone"],
            ),
            "ceo-1",
            "",
        )
        assert res.success is False
        assert "archived" in (res.error or "")
        # 补上发起人后仍少于 2 人拒绝
        res = await start_team_meeting_tool(
            StartTeamMeetingParams(title="t", topics=["q1"], participant_ids=[]),
            "ceo-1",
            "",
        )
        assert res.success is False
        assert "2" in (res.error or "")
        # executor 发起被家族门拒绝（工具体内复核）
        await insert_agent(pid, "dev-2", role="developer")
        res = await start_team_meeting_tool(
            StartTeamMeetingParams(
                title="t", topics=["q1"], participant_ids=["dev-1"]
            ),
            "dev-2",
            "",
        )
        assert res.success is False
        assert "ceo" in (res.error or "")
        # 合法发起：立即返回（无 fan-out 等待），hold 已登记（API 顺序断言）
        res = await start_team_meeting_tool(
            StartTeamMeetingParams(
                title="mtg", topics=["q1"], participant_ids=["dev-1"]
            ),
            "ceo-1",
            "",
        )
        assert res.success is True
        meeting_id = res.extra["meeting_id"]
        assert res.extra["status"] == "assembling"
        assert meeting_hold.is_held("ceo-1")
        assert meeting_hold.is_held("dev-1")
        row = await meeting_service.get_meeting(pid, meeting_id)
        assert row["chair_id"] == "ceo-1"
        assert "dev-1" in row["participants"]
        # 编排器任务已创建（无 live agent 实例 → 全员视为 idle → 自然走完
        # abstain/abort 收尾，不悬挂）；等待其退出，避免泄漏到下一用例。
        task = orchestrator.running_task(meeting_id)
        assert task is not None
        try:
            await asyncio.wait_for(task, timeout=15)
        except Exception:
            pass


def test_start_meeting_accepts_advertised_camel_case():
    """审计 P1 回归锁：TOOL_PARAM_SCHEMAS 宣告 participantIds——LLM 照
    schema 首调必须成功（alias 未接时 validate 报 participant_ids required）。"""
    from hiveweave.tools.base import _TOOL_REGISTRY

    td = _TOOL_REGISTRY["start_team_meeting"]
    validated = td.validate({
        "title": "t",
        "topics": ["a"],
        "participantIds": ["agent-1", "agent-2"],
    })
    args = validated[0] if isinstance(validated, tuple) else validated
    assert args.participant_ids == ["agent-1", "agent-2"]
