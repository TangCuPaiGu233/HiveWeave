"""Agent replay must not store token deltas / bare round_start (orphans → flat noise).

TEST_DSH_44 Bug#2 语义更新（2026-09-05）：round_start 加入 _REPLAY_SKIP_TYPES。
此前 replay 只排除 deltas，裸 round_start 会被重放——但 text_delta 不在缓冲里，
重放的轮次头没有任何伴随内容，前端 beginStreamRound 会插出「中间无内容」的
孤儿轮次分隔线（乱序堆叠第 10/6/7/8/9/10 轮）。轮次结构由前端 live 流 +
DB metadata.segments 快照兜底，replay 环不再缓冲 round_start。
"""
from __future__ import annotations

import pytest

from hiveweave.realtime.event_bus import AGENT_REPLAY_BUFFER, StatusEventBus
from hiveweave.realtime.phoenix_adapter import _map_event


@pytest.mark.asyncio
async def test_replay_skips_token_deltas_and_round_start() -> None:
    bus = StatusEventBus()
    aid = "agent-replay-1"
    for i in range(AGENT_REPLAY_BUFFER + 10):
        await bus.publish_stream_event(aid, {"type": "text_delta", "content": str(i)})
        await bus.publish_stream_event(aid, {"type": "thinking_delta", "content": "t"})
        await bus.publish_stream_event(aid, {"type": "thinking", "elapsed_s": 1})
    await bus.publish_stream_event(aid, {"type": "round_start", "round": 1})
    await bus.publish_stream_event(aid, {"type": "tool_call_start", "tool_name": "bash"})
    await bus.publish_stream_event(aid, {"type": "done"})

    replay = bus.get_agent_replay(aid)
    types = [e["type"] for e in replay]
    assert "text_delta" not in types
    assert "thinking_delta" not in types
    assert "thinking" not in types
    # 裸 round_start 不再重放（无内容噪声）；只留有内容的关键事件
    assert "round_start" not in types
    assert types == ["tool_call_start", "done"]
    assert bus.get_agent_replay(aid) == []


@pytest.mark.asyncio
async def test_round_start_not_buffered_at_all() -> None:
    """TEST_DSH_44 Bug#2: round_start joins the skip set — never occupies the ring."""
    bus = StatusEventBus()
    aid = "agent-replay-2"
    await bus.publish_stream_event(aid, {"type": "round_start", "round": 1})
    for i in range(AGENT_REPLAY_BUFFER + 10):
        await bus.publish_stream_event(aid, {"type": "text_delta", "content": str(i)})
    assert bus.get_agent_replay(aid) == []


@pytest.mark.asyncio
async def test_start_clears_prior_turn_done_from_replay() -> None:
    bus = StatusEventBus()
    aid = "agent-replay-3"
    await bus.publish_stream_event(aid, {"type": "round_start", "round": 0})
    await bus.publish_stream_event(aid, {"type": "done"})
    await bus.publish_stream_event(aid, {"type": "start"})
    await bus.publish_stream_event(aid, {"type": "round_start", "round": 0})
    types = [e["type"] for e in bus.get_agent_replay(aid)]
    assert types == ["start"]
    assert "done" not in types


@pytest.mark.asyncio
async def test_emit_activity_skips_token_deltas_and_round_start() -> None:
    bus = StatusEventBus()
    aid = "agent-replay-4"
    for i in range(5):
        await bus.publish_stream_event(aid, {"type": "text_delta", "content": str(i)})
    await bus.publish_stream_event(aid, {"type": "round_start", "round": 1})
    await bus.publish_stream_event(aid, {"type": "tool_call_start", "tool_name": "bash"})
    types = [e["type"] for e in bus.get_recent_activity()]
    assert "text_delta" not in types
    # 活动环与 replay 同 gate：round_start 不进 lobby recentActivity
    assert "round_start" not in types
    assert "tool_call_start" in types


def test_map_event_round_start_is_not_stream_chunk() -> None:
    name, payload = _map_event({"type": "round_start", "round": 0, "agentId": "a"})
    assert name == "round_start"
    assert name != "stream_chunk"
    assert payload.get("round") == 0
