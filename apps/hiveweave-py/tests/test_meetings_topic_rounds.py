"""团队开会 — 议题串行 × 每题三轮（docs/spec/team-meeting.md §规格验收 D）。"""

from __future__ import annotations

import pytest

from hiveweave.services.meetings import orchestrator
from hiveweave.services.meetings.service import (
    MAX_ROUNDS,
    MeetingError,
    meeting_service,
)
from tests.meeting_env import insert_agent, meeting_env

PID = "mtg-rounds-1"


async def test_state_machine_guards():
    """非法迁移 / r=3 直调 service 也拒 continue / 非主席 conclude 拒绝。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        m = await meeting_service.create_meeting(
            pid, "chair-1", "t", ["topic-1"], ["p-1"]
        )
        mid = m["id"]
        # assembling → facilitating 非法
        with pytest.raises(MeetingError):
            await meeting_service.set_status(pid, mid, "facilitating")
        await meeting_service.set_status(pid, mid, "collecting")
        await meeting_service.set_status(pid, mid, "facilitating")
        # 未 conclude 议题 1 不得开始议题 2 —— cursor 由 service 控制：
        # (topic_index, round_index) 只经 continue/conclude 推进。
        m = await meeting_service.get_meeting(pid, mid)
        assert m["topic_index"] == 0
        # r=1 即可 conclude（不必用满 3 轮）；但先试 r=3 拒 continue
        m = await meeting_service._update(pid, mid, round_index=MAX_ROUNDS)
        with pytest.raises(MeetingError):
            await meeting_service.continue_round(
                pid, mid, "chair-1", "no more rounds"
            )
        # 非主席 conclude 拒绝
        with pytest.raises(MeetingError):
            await meeting_service.conclude_topic(pid, mid, "p-1", "x")
        # 主席 conclude 收口（单题 → concluded + pending）
        final = await meeting_service.conclude_topic(
            pid, mid, "chair-1", "we use asyncio"
        )
        assert final["concluded_now"] is True
        assert final["status"] == "concluded"
        assert final["delivery_state"] == "pending"


async def test_late_speak_and_duplicate_speak_rejected():
    """晚到 speak（已 facilitating）拒绝；同一轮重复发言拒绝。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        m = await meeting_service.create_meeting(
            pid, "chair-1", "t", ["topic-1"], ["p-1", "p-2"]
        )
        mid = m["id"]
        await meeting_service.set_status(pid, mid, "collecting")
        await meeting_service._update(pid, mid, round_index=1)
        await meeting_service.record_utterance(
            pid, mid, topic_index=0, round_index=1,
            agent_id="p-1", role="speech", content="first",
        )
        # 同一轮重复发言拒绝
        with pytest.raises(MeetingError):
            await meeting_service.record_utterance(
                pid, mid, topic_index=0, round_index=1,
                agent_id="p-1", role="speech", content="again",
            )
        await meeting_service.set_status(pid, mid, "facilitating")
        # 晚到 speak（已 facilitating）拒绝
        with pytest.raises(MeetingError):
            await meeting_service.record_utterance(
                pid, mid, topic_index=0, round_index=1,
                agent_id="p-2", role="speech", content="late",
            )


async def test_topic_serial_order_and_multi_topic_flow():
    """未 conclude 议题 1 不得 fan-out 议题 2；方向经 continue 注入。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        seen_status_at_round_start: list[tuple] = []

        async def fake_runner(agent, *, tool_profile, briefing, allow_continue, timeout_s):
            if tool_profile == "participant":
                meeting = await meeting_service.get_meeting(pid, mid)
                # fan-out 只发生在 collecting + cursor 一致时
                seen_status_at_round_start.append(
                    (
                        meeting["status"],
                        meeting["topic_index"],
                        meeting["round_index"],
                        "CONCLUSION-T1" in briefing,
                    )
                )
                return {"action": "speak", "content": f"spoke@t{meeting['topic_index']}r{meeting['round_index']}"}
            meeting = await meeting_service.get_meeting(pid, mid)
            t, r = meeting["topic_index"], meeting["round_index"]
            if (t, r) == (0, 1):
                return {"action": "continue", "direction": "refine T1"}
            return {"action": "conclude", "result": f"CONCLUSION-T{t + 1}"}

        orchestrator.set_runner_fn(fake_runner)
        m = await meeting_service.create_meeting(
            pid, "chair-1", "serial", ["topic-1", "topic-2"], ["p-1"]
        )
        mid = m["id"]
        await orchestrator._orchestrate(pid, mid)
        final = await meeting_service.get_meeting(pid, mid)
        assert final["status"] == "concluded"
        assert [r["title"] for r in final["topic_results"]] == [
            "topic-1", "topic-2",
        ]
        # 议题 2 的 fan-out（t=1）全部发生在议题 1 conclude（T1 结论出现在
        # 简报）之后 —— 串行不被破坏
        t2_starts = [s for s in seen_status_at_round_start if s[1] == 1]
        assert t2_starts, "topic 2 must fan out"
        assert all(s[3] for s in t2_starts), t2_starts
        # continue 只发生在 r<3；r 序列 1 → 2 单调（每轮 2 人：主席也是参会者）
        rounds = [s[2] for s in seen_status_at_round_start if s[1] == 0]
        assert rounds == [1, 1, 2, 2], rounds
