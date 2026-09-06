"""L3 事实总线骨架（repair-plan-20260902.md §L3）。

事实成为一等事件：dispatch/merge/verify 等关键动作产出**带核验时间戳**
的事实，推送 bus 供订阅方（按事实唤醒、工作簿进度推导、观测面板）消费。

设计要点（repair-plan 原文 + 防劣化护栏）：
- 事实是**参考上下文**非义务——执行者可复核任何一条
- 事实必须带核验时间戳——过期事实比没有更危险
- 平台只自动采**廉价可机核**事实（fs/git/http 探测）
- 语义事实（"模块没执行"）必须来自 agent 取证并标注来源

当前状态：骨架（publish/subscribe + 内存分发）。
后续可扩展持久化、按事实唤醒（wait_contract 集成）、远期投影。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Fact:
    """一条已核验事实。

    Attributes:
        kind: 事实类型（merge_landed / task_closed / file_written / …）。
        subject: 事实主体（task_id / file path / branch name 等）。
        payload: 事实详情（结构化，由发布方定义 schema）。
        verified_at: 核验时间戳（epoch ms）——过期事实比没有更危险。
        source: 发布来源标识（"platform" / agent short_id 等）。
    """

    kind: str
    subject: str
    payload: dict[str, Any]
    verified_at: int
    source: str


Subscriber = Callable[[Fact], None]

_subscribers: list[Subscriber] = []
_recent: list[Fact] = []
_MAX_RECENT = 200


def publish(
    kind: str,
    subject: str,
    payload: dict[str, Any] | None = None,
    *,
    source: str = "platform",
) -> Fact:
    """发布一条事实到总线并通知所有订阅方（同步、进程内、fire-and-forget）。"""
    fact = Fact(
        kind=kind,
        subject=subject,
        payload=payload or {},
        verified_at=int(time.time() * 1000),
        source=source,
    )
    _recent.append(fact)
    if len(_recent) > _MAX_RECENT:
        del _recent[: len(_recent) - _MAX_RECENT]
    for sub in _subscribers:
        try:
            sub(fact)
        except Exception as e:
            log.warning("fact_subscriber_error", kind=kind, error=str(e))
    log.debug("fact_published", kind=kind, subject=subject[:80])
    return fact


def subscribe(fn: Subscriber) -> None:
    """注册事实订阅方（同步回调；异常被总线吞掉不阻塞发布方）。"""
    _subscribers.append(fn)


def unsubscribe(fn: Subscriber) -> None:
    try:
        _subscribers.remove(fn)
    except ValueError:
        pass


def recent_facts(kind: str | None = None, limit: int = 20) -> list[Fact]:
    """查询最近事实（可选按 kind 过滤）。观测/工作簿进度推导用。"""
    if kind:
        return [f for f in _recent if f.kind == kind][-limit:]
    return list(_recent[-limit:])


def reset_for_tests() -> None:
    _recent.clear()
    _subscribers.clear()
