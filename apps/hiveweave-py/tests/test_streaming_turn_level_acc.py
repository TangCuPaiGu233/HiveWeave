"""流式 DB 快照必须是 turn 级累计 — round_start 不得清空 content。

此前 on_delta 在 round_start 时把 ``_streaming_text_acc`` 与 DB content
一起清零重写：重连/刷新后前端从 DB 恢复只能看到「当前轮」文本，用户已
看到的前几轮叙述整段消失。前端 beginStreamRound 已是 no-op（整轮时间线
口径），DB 快照与直播 draft 对齐后任何时刻恢复都不回退。

2026-09-01 契约（s3-clone_06「大段旁白」反馈）：turn 级累计保留，
round_start（round>=1）曾在 acc 尾部插入「—— 第 N 轮 ——」分隔行并同步
DB——content 兜底渲染借此获得轮次结构。

2026-09-05 渲染统一：该文本标记下线。轮次结构统一由 round_boundary 段
承载——live 由前端 beginStreamRound 插入、终稿由 build_display_segments
按 tool_turn_acc 轮号标注产出；DB content 回归纯文本兜底，不再含标记行。
"""

from __future__ import annotations

from typing import Any

import pytest

from hiveweave.agents.streaming import on_delta


class _FakeChatMsg:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str | None, dict]] = []

    async def update_message(self, agent_id: str, msg_id: str | None, attrs: dict) -> bool:
        self.updates.append((agent_id, msg_id, attrs))
        return True


class _FakeRunLedger:
    def __init__(self) -> None:
        self.llm_calls: list[str] = []

    async def increment_llm_calls(self, agent_id: str, run_id: str) -> None:
        self.llm_calls.append(run_id)


def _make_agent() -> Any:
    class _Agent:
        id = "a1"
        _streaming_msg_id = "m1"
        _current_run_id = "r1"
        _streaming_text_acc = ""
        _last_stream_activity_at = 0.0
        _chat_msg = _FakeChatMsg()
        _run_ledger = _FakeRunLedger()
        _on_stream_event = None

        def _stop_heartbeat(self) -> None:
            pass

    return _Agent()


@pytest.mark.asyncio
async def test_round_start_keeps_accumulated_text() -> None:
    agent = _make_agent()

    await on_delta(agent, {"type": "round_start", "round": 0})
    await on_delta(agent, {"type": "text_delta", "content": "第一轮旁白。"})
    await on_delta(agent, {"type": "round_start", "round": 1})
    await on_delta(agent, {"type": "text_delta", "content": "第二轮正文。"})

    # turn 级累计：跨轮文本都保留；轮次边界不再往 content 插文本标记
    # （渲染统一——round_boundary 段由 live draft / 终稿 segments 承载）
    assert agent._streaming_text_acc == "第一轮旁白。第二轮正文。"
    content_writes = [
        attrs.get("content")
        for (_, _, attrs) in agent._chat_msg.updates
        if "content" in attrs
    ]
    assert content_writes == [
        "第一轮旁白。",
        "第一轮旁白。第二轮正文。",
    ]
    # 不再有 round_start 触发的 content="" 清空写
    assert "" not in content_writes
    # 任何 DB 快照都不得含轮次文本标记（第三形态已消灭）
    for c in content_writes:
        assert "——" not in c


@pytest.mark.asyncio
async def test_round_start_never_writes_content() -> None:
    """round_start（任意轮号/缺号/非法轮号）都不触发 DB content 写——
    轮次结构改由 round_boundary 段承载，不再污染文本快照。"""
    agent = _make_agent()

    await on_delta(agent, {"type": "round_start", "round": 0})
    assert agent._streaming_text_acc == ""
    assert agent._chat_msg.updates == []

    await on_delta(agent, {"type": "round_start", "round": 1})
    await on_delta(agent, {"type": "round_start"})  # 缺 round 字段
    await on_delta(agent, {"type": "round_start", "round": "x"})  # 非法
    assert agent._streaming_text_acc == ""
    assert agent._chat_msg.updates == []


@pytest.mark.asyncio
async def test_round_start_still_counts_llm_calls() -> None:
    agent = _make_agent()

    await on_delta(agent, {"type": "round_start", "round": 1})

    assert agent._run_ledger.llm_calls == ["r1"]


@pytest.mark.asyncio
async def test_new_turn_placeholder_resets_accumulator() -> None:
    """同一 agent 连续两 turn：round_start 不清零后，turn 起点必须重置
    累积器，否则上一 turn 的全文会泄进本 turn placeholder 的 DB 快照
    （重连恢复时同一段话在两个气泡里重复）。"""
    agent = _make_agent()

    # turn 1
    await on_delta(agent, {"type": "round_start", "round": 0})
    await on_delta(agent, {"type": "text_delta", "content": "turn1 全文"})
    assert agent._streaming_text_acc == "turn1 全文"

    # turn 2：chat() 创建新 placeholder 时重置（agent.py turn 起点逻辑）
    agent._streaming_msg_id = "m2"
    agent._streaming_text_acc = ""

    await on_delta(agent, {"type": "round_start", "round": 0})
    await on_delta(agent, {"type": "text_delta", "content": "turn2 开头"})

    assert agent._streaming_text_acc == "turn2 开头"
    turn2_writes = [
        attrs.get("content")
        for (_, mid, attrs) in agent._chat_msg.updates
        if mid == "m2" and "content" in attrs
    ]
    assert turn2_writes == ["turn2 开头"]
