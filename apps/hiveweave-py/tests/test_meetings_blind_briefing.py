"""团队开会 — 盲评简报形状（docs/spec/team-meeting.md §规格验收 C）。

用唯一标记（canary）探测：参会者简报不含同事 speech 原文；第 2/3 轮含
direction 仍不含原文；议题 2 轮 1 含议题 1 的 topic_result。主席简报含
全部 speech（含弃权）。
"""

from __future__ import annotations

import asyncio

from hiveweave.services.meetings import orchestrator, prompts
from hiveweave.services.meetings.service import meeting_service
from tests.meeting_env import (
    insert_agent,
    meeting_env,
    reset_meeting_state,
)

PID = "mtg-blind-1"
CANARY = "CANARY-SPEECH-_DO_NOT_LEAK_"


def _scripted_runner(pid: str, meeting_id_box: dict, log: list):
    """参会者按名册顺序发言（唯一 canary）；主席按 (topic, round) 决策。"""

    async def fake_runner(agent, *, tool_profile, briefing, allow_continue, timeout_s):
        meeting_id = meeting_id_box.get("id")
        if tool_profile == "participant":
            n = len([e for e in log if e["profile"] == "participant"])
            content = f"{CANARY}-{n}"
            log.append({"profile": tool_profile, "briefing": briefing})
            return {"action": "speak", "content": content}
        log.append({
            "profile": tool_profile,
            "briefing": briefing,
            "allow_continue": allow_continue,
        })
        meeting = await meeting_service.get_meeting(pid, meeting_id)
        t, r = meeting["topic_index"], meeting["round_index"]
        if (t, r) == (0, 1):
            return {
                "action": "continue",
                "direction": "DIR-1 focus on imports only",
            }
        return {"action": "conclude", "result": f"CONCLUSION-T{t + 1}-R{r}"}

    return fake_runner


async def test_participant_briefings_are_blind_to_colleague_speeches():
    """参会者简报：不含任何 speech canary；r=2 含 direction；议题 2 含议题 1 结论。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1", "p-2"):
            await insert_agent(pid, aid)
        entries: list = []
        box: dict = {}
        orchestrator.set_runner_fn(_scripted_runner(pid, box, entries))

        async def fake_start():
            return None

        meeting = await meeting_service.create_meeting(
            pid, "chair-1", "imports", ["topic-1", "topic-2"],
            ["p-1", "p-2"],
        )
        box["id"] = meeting["id"]
        await orchestrator._orchestrate(pid, meeting["id"])
        meeting = await meeting_service.get_meeting(pid, meeting["id"])
        assert meeting["status"] == "concluded"
        assert len(meeting["topic_results"]) == 2

        participant_briefs = [
            e["briefing"] for e in entries if e["profile"] == "participant"
        ]
        chair_briefs = [
            e["briefing"] for e in entries if e["profile"] == "chair"
        ]
        assert participant_briefs and chair_briefs

        # C：参会者简报不含同事（含自己的上一轮）speech 原文
        for b in participant_briefs:
            assert CANARY not in b, f"speech leaked into participant brief: {b}"
        # r=1 无 direction；r=2 含 direction 仍不含原文
        assert "DIR-1" not in participant_briefs[0]
        later = [b for b in participant_briefs if "DIR-1" in b]
        assert later, "round-2 briefings must contain the chair direction"
        for b in later:
            assert CANARY not in b
        # 议题 2 轮 1 含议题 1 的 topic_result，不含议题 1 speech
        t2_briefs = [b for b in participant_briefs if "CONCLUSION-T1" in b]
        assert t2_briefs
        for b in t2_briefs:
            assert CANARY not in b
        # 主席简报含本轮全部 speech（含弃权）
        assert sum(CANARY in b for b in chair_briefs) >= 1
        # 金丝雀：speech 标记不得出现在任何平台组装文本中
        all_briefs = participant_briefs + chair_briefs
        assert all(prompts.SPEECH_MARKER not in b for b in all_briefs)


async def test_result_body_contains_only_conclusions():
    """平台组装只含 topic_results，不拼接 utterances（金丝雀预言二）。"""
    results = [
        {"title": "t1", "result": "use asyncio"},
        {"title": "t2", "result": "no canary here"},
    ]
    body = prompts.result_body(results)
    assert "use asyncio" in body
    assert CANARY not in body
    assert prompts.SPEECH_MARKER not in body


async def test_runner_fn_never_exceeds_parallel_cap():
    """注入 runner_fn 断言 max(in_flight)<=8（规格 G）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        roster = ["chair-1"] + [f"p-{i}" for i in range(20)]
        for aid in roster:
            await insert_agent(pid, aid)
        in_flight = 0
        max_in_flight = 0

        async def fake_runner(agent, *, tool_profile, briefing, allow_continue, timeout_s):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.005)
            in_flight -= 1
            if tool_profile == "participant":
                return {"action": "abstain", "content": "skip"}
            return {"action": "conclude", "result": "done"}

        reset_meeting_state()
        orchestrator.set_runner_fn(fake_runner)
        meeting = await meeting_service.create_meeting(
            pid, "chair-1", "wide", ["only-topic"], roster[1:]
        )
        await orchestrator._orchestrate(pid, meeting["id"])
        assert max_in_flight <= 8, max_in_flight
        final = await meeting_service.get_meeting(pid, meeting["id"])
        assert final["status"] == "concluded"
        # 未发言人（全员 abstain）也进了主席简报（含弃权）
        utters = await meeting_service.get_utterances(
            pid, meeting["id"], topic_index=0, round_index=1,
            roles=("abstain",),
        )
        assert len(utters) >= len(roster) - 1
