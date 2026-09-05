"""团队开会 — 过程遗忘 / Runner 隔离（docs/spec/team-meeting.md §规格验收 E）。

Runner 不走 completion、不 append_turn、不 ACK inbox、不写记忆/日志；
写类工具在执行期白名单外一律 deny。mock 的是 Streamer（规格允许的
mock 面），卡口与 DB 全部真实。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from hiveweave.db import project as project_db
from hiveweave.services.meetings import hold as meeting_hold
from hiveweave.services.meetings import runner as runner_mod
from hiveweave.services.meetings.runner import (
    MEETING_MAX_TOOL_ROUNDS,
    make_on_tool_call,
    run_meeting_turn,
    tools_for_profile,
)
from tests.meeting_env import (
    get_inbox_rows,
    insert_agent,
    insert_inbox_message,
    meeting_env,
)

PID = "mtg-ephemeral-1"


class _FakeStreamer:
    """可控 Streamer 替身：脚本化回调序列与返回值。"""

    def __init__(self, script, max_tool_rounds=None):
        self.script = script
        self.max_tool_rounds = max_tool_rounds
        self.seen_tools = None

    async def stream(self, *, agent_id, messages, model_config, tools,
                     on_tool_call=None, max_tool_rounds=None, **kwargs):
        self.seen_tools = [t["function"]["name"] for t in tools]
        for item in self.script:
            if item.get("sleep"):
                await asyncio.sleep(item["sleep"])
                continue
            name = item["tool"]
            args = json.dumps(item.get("args", {}))
            reply = await on_tool_call(name, args, f"tc-{name}")
            if item.get("expect_end_turn"):
                assert reply.get("end_turn") is True, (name, reply)
        return {"status": "ok", "content": "prose without tool call",
                "usage_rounds": []}


class _FakeAgent:
    def __init__(self, agent_id, project_id):
        self.id = agent_id
        self.project_id = project_id
        self.config = {"name": agent_id, "role": "developer"}
        self.status = "idle"
        self._tool_executor = None
        self.heartbeat = False

    async def _get_workspace_path(self):
        return None

    async def _get_model_config(self):
        return {"model_id": "fake-model", "provider_type": "fake"}

    def _start_heartbeat(self):
        self.heartbeat = True

    def _stop_heartbeat(self):
        self.heartbeat = False

    def _broadcast_status(self, status, extra=None):
        self.status = status


@pytest.fixture
def fake_streamer(monkeypatch):
    def _install(script):
        inst = _FakeStreamer(script)
        monkeypatch.setattr(runner_mod, "Streamer", lambda **kw: inst)
        return inst

    return _install


async def _count(pid: str, table: str, agent_id: str) -> int:
    conn = await project_db.get_project_db_by_project_id(pid)
    col = "agent_id"
    cur = await conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE {col} = ?", [agent_id]
    )
    row = await cur.fetchone()
    await cur.close()
    return row["c"]


async def test_runner_writes_nothing_and_denies_write_tools(fake_streamer):
    """E：会务回合零落库（turns/chat/memory/work_log/inbox-ACK 全无）。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-1")
        # 有一条未读 ask —— runner 结束后必须仍未读（不 ACK inbox）
        await insert_inbox_message(pid, "peer-1", "a-1", "[ASK] ?", expect_report=True)
        agent = _FakeAgent("a-1", pid)
        fake_streamer([
            {"tool": "write_memory", "args": {"content": "leak"}},
            {"tool": "send_message", "args": {"to": "peer-1", "message": "leak"}},
            {"tool": "write_file", "args": {"filePath": "x.py", "content": "leak"}},
            {"tool": "speak_in_meeting", "args": {"content": "my blind speech"},
             "expect_end_turn": True},
        ])
        outcome = await run_meeting_turn(
            agent, tool_profile="participant", briefing="meeting brief"
        )
        assert outcome == {"action": "speak", "content": "my blind speech"}
        # 排他槽已释放
        assert agent.status == "idle"
        assert agent.heartbeat is False
        # 零落库（过程遗忘）
        assert await _count(pid, "conversation_turns", "a-1") == 0
        assert await _count(pid, "chat_messages", "a-1") == 0
        assert await _count(pid, "memories", "a-1") == 0
        assert await _count(pid, "work_logs", "a-1") == 0
        # inbox 未被 ACK
        rows = await get_inbox_rows(pid, "a-1")
        assert rows and rows[0]["read"] == 0


async def test_runner_tool_whitelist_shape(fake_streamer):
    """白名单是替换不是并集：write 工具 deny；r=3 主持无 continue。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        agent = _FakeAgent("c-1", pid)
        holder: dict = {}
        cb = make_on_tool_call(
            agent=agent, executor=None, workspace="", project_root=None,
            whitelist=tools_for_profile("chair", allow_continue=False),
            holder=holder,
        )
        reply = await cb("write_memory", '{"content":"x"}', "t1")
        assert "not available" in reply["content"]
        reply = await cb("continue_meeting_round", '{"direction":"d"}', "t2")
        assert "unavailable" in reply["content"] or "conclude" in reply["content"]
        assert "decision" not in holder
        # 空发言拒绝（不记为发言）
        holder2: dict = {}
        cb2 = make_on_tool_call(
            agent=agent, executor=None, workspace="", project_root=None,
            whitelist=tools_for_profile("participant"), holder=holder2,
        )
        reply = await cb2("speak_in_meeting", '{"content":"  "}', "t3")
        assert "requires non-empty" in reply["content"]
        assert "speech" not in holder2
        # r=3 白名单里没有 continue；MEETING_MAX_TOOL_ROUNDS == 8
        assert "continue_meeting_round" not in tools_for_profile(
            "chair", allow_continue=False
        )
        assert "conclude_topic" in tools_for_profile("chair", allow_continue=False)
        assert MEETING_MAX_TOOL_ROUNDS == 8


async def test_runner_timeout_and_empty_result_become_abstain(fake_streamer):
    """180s 超时 → abstain；不调 speak（散文收尾）→ 不把散文当发言。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        agent = _FakeAgent("a-2", pid)
        # 超时：stream 挂起 → wait_for 超时 → abstain
        fake_streamer([{"sleep": 5.0}])
        outcome = await run_meeting_turn(
            agent, tool_profile="participant", briefing="b", timeout_s=0.05
        )
        assert outcome["action"] == "abstain"
        assert "timed out" in outcome["content"]
        assert agent.status == "idle"
        # 散文收尾（未调 speak）→ abstain，不把 assistant 散文当发言
        fake_streamer([])
        outcome2 = await run_meeting_turn(
            agent, tool_profile="participant", briefing="b", timeout_s=5
        )
        assert outcome2["action"] == "abstain"
        assert "prose without tool call" != outcome2["content"]
        assert "no speak_in_meeting" in outcome2["content"]


async def test_runner_context_read_only_no_history_writeback(fake_streamer):
    """读侧（compacted_prefix + 既有 turns）不产生写回；会务回合无 append_turn。"""
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        await insert_agent(pid, "a-3")
        from hiveweave.conversation.store import conversation_store

        await conversation_store.append_turn(
            "a-3", pid,
            [{"role": "user", "content": "pre-meeting context"}],
        )
        before = await _count(pid, "conversation_turns", "a-3")
        assert before >= 1
        agent = _FakeAgent("a-3", pid)
        fake_streamer([
            {"tool": "speak_in_meeting", "args": {"content": "speech"},
             "expect_end_turn": True},
        ])
        outcome = await run_meeting_turn(
            agent, tool_profile="participant", briefing="b"
        )
        assert outcome["action"] == "speak"
        await asyncio.sleep(0.05)  # 给 store 写队列一个空转机会
        assert await _count(pid, "conversation_turns", "a-3") == before
        meeting_hold.clear_hold_silent("a-3")
