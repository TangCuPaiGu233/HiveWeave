"""件4（42 轮报告）：[VERIFY EXECUTOR STALL] 第 2 次起升级抄送 CEO。

实证形态：同一任务同一收件人 20min 间隔 3 连发，42min 无行动，平台只
重复轰炸原收件人。升级（verify_spawn._send_stall_notice）：
- 原通知追加一行「已升级抄送 CEO（第 N 次）」（第 2 次起）；
- CEO 收 inbox 副本（wake=1、trusted_platform、幂等键含序号防重）；
- 计数来源 = 项目 DB inbox 表同 (task_id, 收件人) 的 STALL 通知行数。
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from hiveweave.db import project as project_db
from hiveweave.db.project import ensure_project_db
from hiveweave.services.inbox import InboxService
from hiveweave.tools.tasks.verify_spawn import (
    _count_prior_stall_notices,
    _pick_ceo_id,
    _send_stall_notice,
)

PROJECT_ID = "test-stall-esc"
COORD = "coord-esc-1"
CEO_ID = "ceo-esc-1"


@pytest.fixture
async def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = str(Path(tmpdir).resolve())

        async def fake_ws(pid: str):
            return workspace_path if pid == PROJECT_ID else None

        async def fake_get_agent_by_id(aid: str):
            return {"id": aid, "project_id": PROJECT_ID, "status": "active"}

        with patch(
            "hiveweave.db.meta.get_project_workspace", fake_ws
        ), patch("hiveweave.db.meta.get_agent_by_id", fake_get_agent_by_id):
            project_db._agent_cache[COORD] = workspace_path
            project_db._agent_cache[CEO_ID] = workspace_path
            await ensure_project_db(workspace_path)
            yield {"workspace_path": workspace_path}

        async with project_db._ensure_lock:
            conn = project_db._cache.pop(workspace_path, None)
            project_db._agent_cache.pop(COORD, None)
            project_db._agent_cache.pop(CEO_ID, None)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


_BY_ID = {
    COORD: {"id": COORD, "parent_id": CEO_ID, "name": "coord", "short_id": "C1"},
    CEO_ID: {
        "id": CEO_ID,
        "parent_id": "",
        "name": "ceo",
        "short_id": "A1",
        "permission_type": "ceo",
        "role": "ceo",
    },
}


def _msg(task_id: str) -> str:
    return (
        f"[VERIFY EXECUTOR STALL] VERIFY 't' ({task_id[:8]}) stuck in "
        f"claimed. No other independent QA."
    )


async def _rows(env, to_agent_id: str, cols: str = "*") -> list[dict]:
    conn = await project_db.get_project_db_for_agent(to_agent_id)
    cur = await conn.execute(
        f"SELECT {cols} FROM inbox WHERE to_agent_id = ? ORDER BY created_at",
        [to_agent_id],
    )
    out = [dict(r) for r in await cur.fetchall()]
    await cur.close()
    return out


def test_pick_ceo_prefers_explicit_ceo_over_parentless_fallback():
    """只认显式 CEO（permission_type/role=ceo）。

    P2-2（审计）：组织数据异常时不得把 wake=1 强唤醒发给无关孤儿 ——
    找不到显式 CEO 返回 ""（放弃 CC + log.warning），不猜 parentless。
    """
    by_id = {
        "coord-x": {"id": "coord-x", "parent_id": None},  # 无上级 ≠ CEO
        CEO_ID: {"id": CEO_ID, "permission_type": "ceo", "parent_id": ""},
    }
    assert _pick_ceo_id(by_id) == CEO_ID
    # 无显式 CEO → 放弃 CC（不再兜底选孤儿 coordinator）
    assert _pick_ceo_id({"coord-x": {"id": "coord-x", "parent_id": None}}) == ""


@pytest.mark.asyncio
async def test_first_notice_no_cc_and_no_escalation_line(env):
    """第 1 次：只发原收件人，无 CEO 副本、无升级行。"""
    tid = str(uuid.uuid4())
    inbox = InboxService()
    await _send_stall_notice(
        inbox,
        project_id=PROJECT_ID,
        task_id=tid,
        to_agent_id=COORD,
        message=_msg(tid),
        agents_by_id=_BY_ID,
    )
    coord_rows = await _rows(env, COORD)
    assert len(coord_rows) == 1
    assert "已升级抄送" not in coord_rows[0]["message"]
    assert await _rows(env, CEO_ID) == []
    assert await _count_prior_stall_notices(PROJECT_ID, tid, COORD) == 1


@pytest.mark.asyncio
async def test_second_notice_appends_line_and_ccs_ceo(env):
    """第 2 次：原通知追加升级行；CEO 收副本（wake=1、trusted、序号幂等键）。"""
    tid = str(uuid.uuid4())
    inbox = InboxService()
    for _ in range(2):
        await _send_stall_notice(
            inbox,
            project_id=PROJECT_ID,
            task_id=tid,
            to_agent_id=COORD,
            message=_msg(tid),
            agents_by_id=_BY_ID,
        )
    coord_rows = await _rows(env, COORD)
    assert len(coord_rows) == 2
    assert "已升级抄送 CEO（第 2 次）" in coord_rows[-1]["message"]

    ceo_rows = await _rows(env, CEO_ID)
    assert len(ceo_rows) == 1
    cc = ceo_rows[0]
    assert cc["wake"] == 1
    assert cc["task_id"] == tid
    assert "[CC]" in cc["message"]
    assert cc["idempotency_key"] == f"verify-stall-cc|{tid}|{COORD}|2"
    # 计数按 inbox 实际行数推进
    assert await _count_prior_stall_notices(PROJECT_ID, tid, COORD) == 2


@pytest.mark.asyncio
async def test_cc_idempotency_key_dedupes(env):
    """幂等键防重：同键重复投递只落一行（deduped=True）。"""
    tid = str(uuid.uuid4())
    key = f"verify-stall-cc|{tid}|{COORD}|2"
    send_kwargs = dict(
        from_agent_id="system",
        to_agent_id=CEO_ID,
        message=f"[VERIFY EXECUTOR STALL][CC] 第 2 次通知 {COORD} 仍无行动。",
        message_type="task",
        priority="urgent",
        task_id=tid,
        wake=True,
        trusted_platform=True,
        idempotency_key=key,
    )
    r1 = await InboxService().send_message(**send_kwargs)
    r2 = await InboxService().send_message(**send_kwargs)
    rows = await _rows(env, CEO_ID)
    assert len(rows) == 1
    assert r1["id"] == rows[0]["id"]
    assert r2.get("deduped") is True or r2["id"] == r1["id"]
