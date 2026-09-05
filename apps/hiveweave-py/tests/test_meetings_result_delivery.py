"""团队开会 — RESULT 与回岗（docs/spec/team-meeting.md §规格验收 F）。"""

from __future__ import annotations

import time

from hiveweave.services.meetings import hold as meeting_hold
from hiveweave.services.meetings import orchestrator, prompts
from hiveweave.services.meetings.service import meeting_service
from tests.meeting_env import (
    get_inbox_rows,
    get_wait_row,
    insert_agent,
    insert_inbox_message,
    insert_wait,
    meeting_env,
)

PID = "mtg-result-1"
RESULT_TAG = prompts.MEETING_RESULT_TAG
CONCLUSION = "CONCLUSION-we-use-asyncio"
SPEECH_CANARY = "CANARY-SPEECH-NEVER-IN-RESULT"


def _conclude_runner():
    async def fake_runner(agent, *, tool_profile, briefing, allow_continue, timeout_s):
        if tool_profile == "participant":
            return {"action": "speak", "content": SPEECH_CANARY}
        return {"action": "conclude", "result": CONCLUSION}

    return fake_runner


async def _start_and_run(pid: str, topics=None):
    orchestrator.set_runner_fn(_conclude_runner())
    meeting = await orchestrator.start_meeting(
        pid, "chair-1", "roundtable", topics or ["topic-1"], ["p-1"]
    )
    task = orchestrator.running_task(meeting["id"])
    await task
    return await meeting_service.get_meeting(pid, meeting["id"])


async def test_result_delivery_and_return_to_desk():
    """散会：RESULT(wake=1, 非 ask) + unpark 原行 + wait 补时 + 清 hold +
    delivered；正文无 speech 金丝雀；重复投递幂等。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        # 开会前的岗位帧：一条 ask（expect_report）+ 一个未到期 wait
        ask = await insert_inbox_message(
            pid, "boss-1", "p-1", "[ASK] desk work", expect_report=True
        )
        original_exp = int(time.time() * 1000) + 60_000
        wait_id = await insert_wait(pid, "p-1", expires_at=original_exp)

        final = await _start_and_run(pid)
        assert final["status"] == "concluded"
        assert final["delivery_state"] == "delivered"

        rows = await get_inbox_rows(pid, "p-1")
        results = [r for r in rows if (r["message"] or "").startswith(RESULT_TAG)]
        assert len(results) == 1, rows
        result = results[0]
        assert result["wake"] == 1 and result["read"] == 0
        assert not result["expect_report"]  # 显式非 ask
        assert CONCLUSION in result["message"]
        assert SPEECH_CANARY not in result["message"]  # 平台正文无 speech
        # unpark：原 ask 行恢复可 trigger，未被 mark_read / 折叠
        restored = next(r for r in rows if r["id"] == ask["id"])
        assert restored["read"] == 0 and restored["wake"] == 1
        assert restored["parked"] == 0
        assert restored["expect_report"] == 1
        # wait 补时（开会时间不计入等待）；未被批量超时
        wrow = await get_wait_row(pid, wait_id)
        assert wrow["cleared_at"] is None
        assert wrow["expires_at"] >= original_exp
        # hold 已清
        assert not meeting_hold.is_held("p-1")
        assert not meeting_hold.is_held("chair-1")
        # 主席也收到 RESULT（回岗不经第二次 runner）
        chair_rows = await get_inbox_rows(pid, "chair-1")
        assert any(
            (r["message"] or "").startswith(RESULT_TAG) for r in chair_rows
        )
        # 重复投递幂等（泵重试不重复 inbox）
        await orchestrator._deliver_and_return(pid, final["id"])
        rows2 = await get_inbox_rows(pid, "p-1")
        results2 = [
            r for r in rows2 if (r["message"] or "").startswith(RESULT_TAG)
        ]
        assert len(results2) == 1


async def test_pump_delivers_pending_concluded_once():
    """concluded+delivery_pending 无 inbox（崩溃模拟）→ 泵只投递一次。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        m = await meeting_service.create_meeting(
            pid, "chair-1", "crash", ["only"], ["p-1"]
        )
        await meeting_service.set_status(pid, m["id"], "collecting")
        await meeting_service._update(pid, m["id"], round_index=1)
        final = await meeting_service.conclude_topic(
            pid, m["id"], "chair-1", CONCLUSION
        )
        assert final["delivery_state"] == "pending"
        assert not (await get_inbox_rows(pid, "p-1"))

        stats = await orchestrator.recover_meetings(pid)
        assert stats["delivered"] == 1
        rows = await get_inbox_rows(pid, "p-1")
        results = [
            r for r in rows if (r["message"] or "").startswith(RESULT_TAG)
        ]
        assert len(results) == 1
        delivered = await meeting_service.get_meeting(pid, m["id"])
        assert delivered["delivery_state"] == "delivered"
        # 再跑泵：已 delivered 不重投
        stats2 = await orchestrator.recover_meetings(pid)
        assert stats2["delivered"] == 0
        rows2 = await get_inbox_rows(pid, "p-1")
        assert len([
            r for r in rows2 if (r["message"] or "").startswith(RESULT_TAG)
        ]) == 1
