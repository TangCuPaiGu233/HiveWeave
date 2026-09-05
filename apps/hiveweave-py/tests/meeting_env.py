"""团队开会测试共享环境（真实 service + 临时 project DB）。

风格契约（docs/spec/team-meeting.md §规格验收）：mock 的是 LLM /
Streamer / 编排器 runner_fn，**不** mock Agent.chat() 卡口与 DB。
"""

from __future__ import annotations

import contextlib
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hiveweave.db import meta as meta_db
from hiveweave.db import project as project_db
from hiveweave.services.agent_router import AgentRoute, agent_router
from hiveweave.services.meetings import hold as meeting_hold
from hiveweave.services.meetings import orchestrator


def _now_ms() -> int:
    return int(time.time() * 1000)


def reset_meeting_state() -> None:
    """清空跨用例的进程内会务状态（hold 注册表 / 运行中编排任务 / 注入）。"""
    meeting_hold.reset_holds_for_tests()
    orchestrator._RUNNING.clear()
    orchestrator.set_runner_fn(None)


@contextlib.asynccontextmanager
async def meeting_env(project_id: str):
    """临时 workspace + meta 路由 patch（test11 task_env 同款）。"""
    reset_meeting_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = str(Path(tmpdir).resolve())

        async def fake_ws(pid: str):
            return workspace_path if pid == project_id else None

        with patch("hiveweave.db.meta.get_project_workspace", fake_ws):
            try:
                yield {
                    "project_id": project_id,
                    "workspace": workspace_path,
                }
            finally:
                reset_meeting_state()
                async with project_db._ensure_lock:
                    conn = project_db._cache.pop(workspace_path, None)
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:
                        pass


async def insert_agent(
    project_id: str,
    agent_id: str,
    *,
    name: str = "",
    role: str = "developer",
    permission_type: str = "executor",
    status: str = "active",
    parent_id: str | None = None,
) -> None:
    """直接插 agents 行（路由注册 + roster/dismiss 判定用）。"""
    await project_db.execute_by_project(
        project_id,
        "INSERT INTO agents (id, short_id, project_id, name, role, "
        "permission_type, status, parent_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            agent_id,
            agent_id[:4].upper(),
            project_id,
            name or agent_id,
            role,
            permission_type,
            status,
            parent_id,
            _now_ms(),
        ],
    )
    agent_router.register(
        AgentRoute(
            agent_id=agent_id,
            project_id=project_id,
            workspace_path="",
            short_id=agent_id[:4].upper(),
            name=name or agent_id,
            role=role,
            status=status,
        )
    )


def agent_row(
    role: str = "developer",
    permission_type: str = "executor",
    family: str | None = None,
) -> dict[str, Any]:
    """policy 判定用的最小 agent dict（infer_role_family 输入形状）。"""
    row: dict[str, Any] = {
        "role": role,
        "permission_type": permission_type,
    }
    if family is not None:
        row["role_family"] = family
    return row


async def insert_inbox_message(
    project_id: str,
    from_id: str,
    to_id: str,
    message: str,
    *,
    message_type: str = "normal",
    expect_report: bool = False,
    wake: bool = True,
) -> dict:
    from hiveweave.services.inbox import InboxService

    return await InboxService().send_message(
        from_agent_id=from_id,
        to_agent_id=to_id,
        message=message,
        message_type=message_type,
        expect_report=expect_report,
        wake=wake,
        trusted_platform=True,
    )


async def get_inbox_rows(project_id: str, agent_id: str) -> list[dict]:
    conn = await project_db.get_project_db_by_project_id(project_id)
    cur = await conn.execute(
        "SELECT * FROM inbox WHERE to_agent_id = ? ORDER BY created_at ASC",
        [agent_id],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    await cur.close()
    return rows


async def get_wait_row(project_id: str, wait_id: str) -> dict | None:
    conn = await project_db.get_project_db_by_project_id(project_id)
    cur = await conn.execute(
        "SELECT * FROM agent_waits WHERE id = ?", [wait_id]
    )
    row = await cur.fetchone()
    await cur.close()
    return dict(row) if row is not None else None


async def insert_wait(
    project_id: str,
    agent_id: str,
    *,
    kind: str = "agent",
    ref: str = "peer-1",
    expires_at: int | None = None,
) -> str:
    wait_id = str(uuid.uuid4())
    await project_db.execute_by_project(
        project_id,
        "INSERT INTO agent_waits (id, agent_id, project_id, kind, ref, "
        "wake_on, expires_at, created_at) VALUES (?, ?, ?, ?, ?, '[]', ?, ?)",
        [wait_id, agent_id, project_id, kind, ref, expires_at, _now_ms()],
    )
    return wait_id
