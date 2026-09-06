"""平台级助理系统工作区测试（spec §7，决策 D6）。

覆盖：
- ensure_assistant 幂等创建：Meta DB 项目行（is_started=1）+ 数据根下
  工作区 + per-project DB + 助理 agent（role=ceo, active）
- 幂等：二次调用不重复创建
- chat_with_assistant：mock agent 注入 AgentManager → 消息经
  deliver_user_message 投递，metadata.source=ball 落库，LLM 收到
  BUG-036 用户信封
- resolve_project_ceo：role=ceo 且 active（D2 口径）
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from hiveweave.agents import types as agent_types
from hiveweave.agents import supervisor as supervisor_mod
from hiveweave.agents.supervisor import agent_manager
from hiveweave.config import get_assistant_workspace
from hiveweave.db import meta as meta_db
from hiveweave.db import project as project_db
from hiveweave.services import assistant as assistant_mod
from hiveweave.services.assistant import (
    ASSISTANT_AGENT_ID,
    ASSISTANT_PROJECT_ID,
    chat_with_assistant,
    ensure_assistant,
    resolve_project_ceo,
)


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeAgent:
    """最小 agent 替身：deliver_user_message 直聊路径所需的面。"""

    def __init__(self) -> None:
        self.id = ASSISTANT_AGENT_ID
        self.project_id = ASSISTANT_PROJECT_ID
        self.status = _FakeStatus("idle")
        self.chat = AsyncMock(return_value={"ok": True})
        self._on_stream_event = None
        self._on_status_change = None


@pytest.fixture(autouse=True)
async def _isolated_meta_db(monkeypatch, tmp_path):
    """每用例独立 Meta DB + 数据根（env HIVEWEAVE_DATA_ROOT）。"""
    import sys

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("HIVEWEAVE_DATA_ROOT", str(tmp_path / "data-root"))
    monkeypatch.setattr(
        meta_db.app_settings,
        "meta_db_path",
        str(tmp_path / "meta" / "hiveweave.db"),
    )
    await meta_db.close_meta_db()
    await meta_db.init_meta_db()
    agent_manager._agents.clear()
    yield
    agent_manager._agents.clear()


async def test_ensure_assistant_creates_workspace_project_and_agent():
    result = await ensure_assistant()

    assert result["project_id"] == ASSISTANT_PROJECT_ID
    assert result["agent_id"] == ASSISTANT_AGENT_ID
    # 工作区在数据根下（§11：助理工作区一律经数据根取路径）
    assert result["workspace"] == str(get_assistant_workspace())

    row = await meta_db.query_one(
        "SELECT id, workspace_path, is_started, name FROM projects WHERE id = ?",
        [ASSISTANT_PROJECT_ID],
    )
    assert row is not None
    assert row["workspace_path"] == str(get_assistant_workspace())
    assert bool(row["is_started"]) is True  # 助理不随全局下班停摆
    assert row["name"] == "__assistant__"

    conn = await project_db.ensure_project_db(str(get_assistant_workspace()))
    cursor = await conn.execute(
        "SELECT id, role, status, permission_type, name FROM agents WHERE id = ?",
        [ASSISTANT_AGENT_ID],
    )
    agent_row = await cursor.fetchone()
    await cursor.close()
    assert agent_row is not None
    assert agent_row["role"] == "ceo"  # D6：助理 = 系统工作区 CEO
    assert agent_row["status"] == "active"
    assert agent_row["name"] == "助理"


async def test_ensure_assistant_is_idempotent():
    first = await ensure_assistant()
    assert first["created_project"] is True
    assert first["created_agent"] is True

    second = await ensure_assistant()
    assert second["created_project"] is False
    assert second["created_agent"] is False

    rows = await meta_db.query(
        "SELECT id FROM projects WHERE id = ?", [ASSISTANT_PROJECT_ID]
    )
    assert len(rows) == 1
    conn = await project_db.ensure_project_db(str(get_assistant_workspace()))
    cursor = await conn.execute(
        "SELECT id FROM agents WHERE id = ?", [ASSISTANT_AGENT_ID]
    )
    agent_rows = await cursor.fetchall()
    await cursor.close()
    assert len(agent_rows) == 1


async def test_ensure_assistant_repositions_workspace_on_data_root_move(
    monkeypatch, tmp_path
):
    """数据根迁移（env 改变）→ 助理工作区跟随数据根。"""
    await ensure_assistant()
    monkeypatch.setenv(
        "HIVEWEAVE_DATA_ROOT", str(tmp_path / "data-root-2")
    )
    result = await ensure_assistant()
    assert result["workspace"] == str(get_assistant_workspace())
    row = await meta_db.query_one(
        "SELECT workspace_path FROM projects WHERE id = ?",
        [ASSISTANT_PROJECT_ID],
    )
    assert row["workspace_path"] == str(get_assistant_workspace())


async def test_chat_with_assistant_delivers_via_pipeline(monkeypatch):
    """对话入口：存 chat_messages(metadata.source=ball) + 信封投给 LLM。"""
    await ensure_assistant()
    fake = _FakeAgent()
    agent_manager._agents[ASSISTANT_AGENT_ID] = fake

    result = await chat_with_assistant("你好呀", source="ball")

    assert result["ok"] is True
    assert result["outcome"] == "started"
    assert result["agentId"] == ASSISTANT_AGENT_ID
    fake.chat.assert_awaited_once()
    envelope = json.loads(fake.chat.await_args.args[0])
    assert envelope["from"] == "用户"
    assert envelope["content"] == "你好呀"

    # 消息确实落库且带 source=ball（三面同管道，来源可辨）
    rows = await project_db.query(
        ASSISTANT_AGENT_ID,
        "SELECT role, content, metadata FROM chat_messages WHERE agent_id = ?",
        [ASSISTANT_AGENT_ID],
    )
    assert len(rows) == 1
    assert rows[0]["role"] == "user"
    assert rows[0]["content"] == "你好呀"
    assert json.loads(rows[0]["metadata"])["source"] == "ball"


async def test_chat_with_assistant_busy_queues_via_inbox(monkeypatch):
    """助理忙 → InboxService 排队（from=用户，trusted_platform）。"""
    from hiveweave.services import inbox as inbox_mod

    await ensure_assistant()
    fake = _FakeAgent()
    fake.status = _FakeStatus("processing")
    agent_manager._agents[ASSISTANT_AGENT_ID] = fake

    send_mock = AsyncMock(return_value={"id": "m1"})
    monkeypatch.setattr(
        inbox_mod.InboxService, "send_message", send_mock
    )

    result = await chat_with_assistant("排队消息", source="ball")

    assert result["ok"] is True
    assert result["outcome"] == "queued"
    fake.chat.assert_not_awaited()
    send_mock.assert_awaited_once()
    kwargs = send_mock.await_args.kwargs
    assert kwargs["from_agent_id"] == "用户"
    assert kwargs["to_agent_id"] == ASSISTANT_AGENT_ID
    assert kwargs["message"] == "排队消息"
    assert kwargs["trusted_platform"] is True


async def test_resolve_project_ceo_active_role_wins():
    """D2 口径：项目内 role=ceo 且 active 动态解析。"""
    from hiveweave.services.org import OrgService

    await ensure_assistant()
    # 助理系统工作区自身的 CEO = 助理
    ceo = await resolve_project_ceo(ASSISTANT_PROJECT_ID)
    assert ceo is not None
    assert ceo["id"] == ASSISTANT_AGENT_ID

    # 用户项目：两个 CEO 行时 active 的胜出，archived 的不解析
    ws = str(tmp_user_workspace())
    await meta_db.execute(
        "INSERT INTO projects (id, name, workspace_path, created_at, "
        "is_started) VALUES (?, ?, ?, ?, 1)",
        ["proj-u1", "用户项目", ws, 1],
    )
    await project_db.ensure_project_db(ws)
    org = OrgService()
    await org.create_agent(
        {
            "id": "ceo-archived",
            "project_id": "proj-u1",
            "name": "旧CEO",
            "role": "ceo",
            "status": "archived",
            "permission_type": "coordinator",
        },
        bootstrap=True,
    )
    await org.create_agent(
        {
            "id": "ceo-active",
            "project_id": "proj-u1",
            "name": "新CEO",
            "role": "ceo",
            "status": "active",
            "permission_type": "coordinator",
        },
        bootstrap=True,
    )
    ceo2 = await resolve_project_ceo("proj-u1")
    assert ceo2 is not None
    assert ceo2["id"] == "ceo-active"


def tmp_user_workspace():
    from hiveweave.config import get_data_root

    ws = get_data_root() / "user-project-ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


async def test_agent_state_enum_contract():
    """sanity：deliver 判忙用的状态枚举值仍是 processing。"""
    assert agent_types.AgentState.PROCESSING.value == "processing"
    assert supervisor_mod.AgentState.IDLE.value == "idle"
