"""悬浮球红点桥 — 出站消息 → 球未读事件（spec §4.2 / §10）。

§4.2 修正记录：出站**不只镜像 message_user**——助理/CEO 的正文流回复
也推（正文流与 message_user 在事件总线已天然汇聚：正文经
``publish_stream_event`` 的 ``done`` 事件进 lobby 频道，message_user 经
``publish_chat_message`` 进 chat 频道）。本桥同时订阅两个频道，按
ball 追踪面（助理 + 各项目 CEO）过滤，维护未读计数并把红点事件发布到
``"ball"`` 频道（``status_event_bus.publish("ball", ...)``）。

- thinking / 工具调用过程不推（§4.2）；text_delta 洪水不订阅。
- 追踪面动态解析（D2 口径）：助理固定 + 各项目 ``role=ceo 且 active``，
  缓存 30s 防每次事件打 DB。
- 球端消费方式（P0）：轮询 ``/api/ball/unread``（进程内计数）；
  ``"ball"`` 频道事件供 P1+ 飞书镜像器/WS 推送复用。
"""

from __future__ import annotations

import asyncio
import time

import structlog

log = structlog.get_logger(__name__)

_TRACKED_TTL_MS = 30_000
_DEDUPE_WINDOW_MS = 2_000

_UNREAD: dict[str, dict] = {}
"""agent_id → {"count": int, "last": str, "at": ms}（进程内未读计数）。"""

_subscribed = False
_pump_task: asyncio.Task | None = None
_tracked_cache: tuple[list[str], float] = ([], 0.0)


def _note_unread(agent_id: str, content: str) -> None:
    rec = _UNREAD.setdefault(agent_id, {"count": 0, "last": "", "at": 0})
    rec["count"] += 1
    rec["last"] = (content or "")[:120]
    rec["at"] = int(time.time() * 1000)


def get_unread_counts() -> dict[str, int]:
    """{agent_id: unread_count} 快照（REST /api/ball/unread 用）。"""
    return {aid: rec["count"] for aid, rec in _UNREAD.items() if rec["count"] > 0}


def clear_unread(agent_id: str | None = None) -> int:
    """清零（展开面板即已读）。agent_id 为空清全部。返回清除的计数。"""
    if agent_id is None:
        total = sum(rec["count"] for rec in _UNREAD.values())
        _UNREAD.clear()
        return total
    rec = _UNREAD.pop(agent_id, None)
    return rec["count"] if rec else 0


async def _resolve_tracked() -> list[str]:
    """解析 ball 追踪面 agent 集合（助理 + 各项目 CEO，缓存 30s）。"""
    global _tracked_cache
    now = time.monotonic()
    cached, at = _tracked_cache
    if cached and (now - at) < _TRACKED_TTL_MS / 1000:
        return cached

    tracked: list[str] = []
    try:
        from hiveweave.services.assistant import (
            ASSISTANT_AGENT_ID,
            ASSISTANT_PROJECT_ID,
            resolve_project_ceo,
        )
        from hiveweave.db import meta as meta_db

        tracked.append(ASSISTANT_AGENT_ID)
        rows = await meta_db.query(
            "SELECT id FROM projects WHERE id != ?", [ASSISTANT_PROJECT_ID]
        )
        for r in rows:
            ceo = await resolve_project_ceo(r["id"])
            if ceo and ceo.get("id"):
                tracked.append(str(ceo["id"]))
    except Exception as e:
        log.warning("ball_bridge_tracked_resolve_failed", error=str(e))
        tracked = cached  # 解析失败沿用旧集合，不空窗
    _tracked_cache = (tracked, now)
    return tracked


async def _handle_bus_event(event: dict) -> None:
    """过滤总线事件 → 未读计数 + ``"ball"`` 频道红点事件。

    处理两类出站（§4.2）：
    - ``done``（正文流收口，lobby 频道）：content 非空才算用户可读输出；
    - ``chat_message``（chat 频道，message_user/团队消息镜像）：
      role=assistant 且 content 非空。
    短窗口 (agentId, content) 去重——同一句话两个频道都出现时只计一次。
    """
    etype = event.get("type", "")
    content = ""
    if etype == "done":
        content = str(event.get("content") or "")
    elif etype == "chat_message":
        if event.get("role") != "assistant":
            return
        content = str(event.get("content") or "")
    else:
        return
    if not content.strip():
        return

    agent_id = str(event.get("agentId") or "")
    if not agent_id:
        return
    tracked = await _resolve_tracked()
    if agent_id not in tracked:
        return

    # 短窗口去重（done 与 chat_message 汇聚同一句时）
    rec = _UNREAD.get(agent_id)
    now_ms = int(time.time() * 1000)
    if (
        rec
        and rec["last"]
        and rec["last"] == content[:120]
        and (now_ms - rec["at"]) < _DEDUPE_WINDOW_MS
    ):
        return

    _note_unread(agent_id, content)
    try:
        from hiveweave.realtime.event_bus import status_event_bus

        await status_event_bus.publish(
            "ball",
            {
                "type": "ball_unread",
                "agentId": agent_id,
                "preview": content[:120],
                "count": _UNREAD.get(agent_id, {}).get("count", 1),
            },
        )
    except Exception as e:
        log.warning("ball_bridge_publish_failed", error=str(e))


async def _pump(queue: asyncio.Queue) -> None:
    """排空订阅队列 → 逐事件处理（进程生命周期随 loop）。"""
    while True:
        event = await queue.get()
        try:
            await _handle_bus_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("ball_bridge_event_failed", error=str(e))


async def start_ball_bridge() -> bool:
    """订阅 lobby + chat 频道并启动泵任务（幂等，失败返回 False）。"""
    global _subscribed, _pump_task
    if _subscribed:
        return True
    try:
        from hiveweave.realtime.event_bus import status_event_bus

        lobby_q = await status_event_bus.subscribe("lobby")
        chat_q = await status_event_bus.subscribe("chat")
        _pump_task = asyncio.gather(
            _pump(lobby_q),
            _pump(chat_q),
        )
        _subscribed = True
        log.info("ball_bridge_started")
        return True
    except Exception as e:
        log.warning("ball_bridge_start_failed", error=str(e))
        return False


def stop_ball_bridge() -> None:
    """停泵 + 退订（shutdown/测试复位用）。"""
    global _subscribed, _pump_task, _tracked_cache
    if _pump_task is not None:
        _pump_task.cancel()
        _pump_task = None
    _subscribed = False
    _tracked_cache = ([], 0.0)


def reset_for_tests() -> None:
    """测试复位：清计数 + 停泵 + 清缓存（不动总线订阅队列清理——
    conftest 的 close/evict 兜底覆盖连接类资源；这里只管本模块状态）。"""
    _UNREAD.clear()
    stop_ball_bridge()
