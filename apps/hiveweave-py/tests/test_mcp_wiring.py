"""45 轮 #9 MCP 接线核心：工具桥命名/两阶段同步/退避降级/绑定即用/分流。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hiveweave.services import mcp_supervisor as sup
from hiveweave.services.permission import PermissionService


@pytest.fixture(autouse=True)
def _clean_supervisor():
    sup.reset_for_tests()
    yield
    sup.reset_for_tests()


# ── 公开名规则（DSH tools.ts 同款）────────────────────────────


def test_public_tool_name_simple():
    assert sup.public_tool_name("fs", "read_file") == "mcp__fs__read_file"


def test_public_tool_name_sanitizes_and_hashes():
    name = sup.public_tool_name("my server", "read file.txt")
    assert name.startswith("mcp__my_server__read_file_txt")
    assert len(name) <= sup.MAX_PUBLIC_NAME_LENGTH


def test_public_tool_name_long_gets_hash_suffix():
    raw = "x" * 200
    name = sup.public_tool_name("srv", raw)
    assert len(name) <= sup.MAX_PUBLIC_NAME_LENGTH
    assert name != sup.public_tool_name("srv", "y" * 200)  # 不碰撞


# ── 两阶段原子同步 ────────────────────────────────────────────


def _patch_list_tools(mock):
    return patch(
        "hiveweave.services.mcp.mcp_service.list_tools", new=mock
    )


@pytest.mark.asyncio
async def test_sync_server_success_populates_table():
    mock = AsyncMock(return_value=[
        {"name": "read", "description": "Read a file", "inputSchema": {"type": "object"}},
        {"name": "write", "description": "", "inputSchema": None},
    ])
    with _patch_list_tools(mock):
        assert await sup.sync_server("fs") is True
    e = sup.get_tool_entry("mcp__fs__read")
    assert e is not None and e.raw_name == "read"
    assert e.input_schema == {"type": "object"}
    # 空 schema 缺省 → None entry schema 仍建表（执行层 agent defs 兜底）
    assert sup.get_tool_entry("mcp__fs__write") is not None
    assert sup.server_tool_names("fs") == ["mcp__fs__read", "mcp__fs__write"]


@pytest.mark.asyncio
async def test_sync_failure_keeps_previous_generation():
    mock = AsyncMock(return_value=[
        {"name": "read", "description": "v1", "inputSchema": {}},
    ])
    with _patch_list_tools(mock):
        await sup.sync_server("fs")
    # 第二代同步失败（server 内重名 → 整代无效）
    bad = AsyncMock(return_value=[
        {"name": "dup", "description": "", "inputSchema": {}},
        {"name": "dup", "description": "", "inputSchema": {}},
    ])
    with _patch_list_tools(bad):
        assert await sup.sync_server("fs", force=True) is False
    # 上一代保留（原子性）
    e = sup.get_tool_entry("mcp__fs__read")
    assert e is not None and e.description == "v1"


@pytest.mark.asyncio
async def test_degraded_after_max_attempts_drops_tools():
    mock = AsyncMock(return_value=[
        {"name": "read", "description": "v1", "inputSchema": {}},
    ])
    with _patch_list_tools(mock):
        await sup.sync_server("fs")
    err = AsyncMock(side_effect=RuntimeError("connection refused"))
    with _patch_list_tools(err):
        for _ in range(sup.MAX_ATTEMPTS):
            await sup.sync_server("fs", force=True)
    # 降级 = fail-closed 可见：工具从表里摘除
    assert sup.get_tool_entry("mcp__fs__read") is None
    assert sup.server_tool_names("fs") == []
    # 成功同步恢复
    with _patch_list_tools(mock):
        await sup.sync_server("fs", force=True)
    assert sup.get_tool_entry("mcp__fs__read") is not None


@pytest.mark.asyncio
async def test_backoff_gate_blocks_immediate_retry():
    err = AsyncMock(side_effect=RuntimeError("down"))
    with _patch_list_tools(err):
        assert await sup.sync_server("fs") is False
        # 退避闸门：未 force 的立即重试不做尝试直接 False
        assert await sup.sync_server("fs") is False
    assert err.await_count == 1


# ── 绑定即用判定 + executor 分流 ──────────────────────────────


@pytest.mark.asyncio
async def test_agent_can_use_requires_binding_and_table():
    mock = AsyncMock(return_value=[
        {"name": "read", "description": "", "inputSchema": {}},
    ])
    with _patch_list_tools(mock):
        await sup.sync_server("fs")
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=["fs"]),
    ):
        assert await sup.agent_can_use("a1", "mcp__fs__read") is True
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=[]),
    ):
        assert await sup.agent_can_use("a1", "mcp__fs__read") is False
    assert await sup.agent_can_use("a1", "mcp__other__read") is False


def _mk_executor():
    from hiveweave.tools.executor import ToolExecutor

    ex = ToolExecutor.__new__(ToolExecutor)
    ex.permission = PermissionService()
    return ex


@pytest.mark.asyncio
async def test_execute_mcp_tool_success_and_unbound():
    sup._tool_table["mcp__fs__read"] = sup.McpToolEntry(
        server="fs", raw_name="read", description="", input_schema={}
    )
    ex = _mk_executor()
    ex.permission.evaluate_detailed = AsyncMock(return_value=("allow", None))
    with (
        patch(
            "hiveweave.services.mcp.mcp_service.get_bound_mcp",
            new=AsyncMock(return_value=["fs"]),
        ),
        patch(
            "hiveweave.services.mcp.mcp_service.call_tool",
            new=AsyncMock(return_value="file contents here"),
        ),
    ):
        result = await ex._execute_mcp_tool("a1", "mcp__fs__read", {"path": "x"})
    assert result["success"] is True
    assert result["output"] == "file contents here"

    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=[]),
    ):
        result = await ex._execute_mcp_tool("a1", "mcp__fs__read", {})
    assert result["success"] is False
    assert "not bound" in result["error"]


@pytest.mark.asyncio
async def test_execute_mcp_tool_unknown_gives_actionable_error():
    ex = _mk_executor()
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=[]),
    ):
        result = await ex._execute_mcp_tool("a1", "mcp__nope__x", {})
    assert result["success"] is False
    assert "not synced or degraded" in result["error"]
    assert "RETRY" in result["error"]


# ── 权限：绑定即用分支 ────────────────────────────────────────


def _agent_row(**kw):
    return {
        "role": "开发者",
        "name": "nova",
        "permission_type": "executor",
        "permission_mode": "readwrite",
        **kw,
    }


@pytest.mark.asyncio
async def test_permission_bound_mcp_allowed_unbound_denied(monkeypatch):
    async def fake_get(_aid):
        return _agent_row()

    monkeypatch.setattr(
        "hiveweave.services.permission.meta_db.get_agent_by_id", fake_get
    )
    svc = PermissionService()
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=["fs"]),
    ):
        # 表里没这条目 → deny（绑定≠存在）
        decision, reason = await svc.evaluate_detailed("a1", "mcp__fs__read")
        assert decision == "deny"

    sup._tool_table["mcp__fs__read"] = sup.McpToolEntry(
        server="fs", raw_name="read", description="", input_schema={}
    )
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=["fs"]),
    ):
        decision, _ = await svc.evaluate_detailed("a1", "mcp__fs__read")
        assert decision == "allow"
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=[]),
    ):
        decision, reason = await svc.evaluate_detailed("a1", "mcp__fs__read")
        assert decision == "deny"
        assert "not bound" in (reason or "")


@pytest.mark.asyncio
async def test_permission_denied_glob_still_beats_binding(monkeypatch):
    """通用门禁优先：denied_tools glob 命中时绑定也拦（无 MCP 特殊分支）。"""
    async def fake_get(_aid):
        return _agent_row(denied_tools='["mcp__fs__*"]')

    monkeypatch.setattr(
        "hiveweave.services.permission.meta_db.get_agent_by_id", fake_get
    )
    svc = PermissionService()
    sup._tool_table["mcp__fs__read"] = sup.McpToolEntry(
        server="fs", raw_name="read", description="", input_schema={}
    )
    with patch(
        "hiveweave.services.mcp.mcp_service.get_bound_mcp",
        new=AsyncMock(return_value=["fs"]),
    ):
        decision, reason = await svc.evaluate_detailed("a1", "mcp__fs__read")
        assert decision == "deny"
        assert "denied_tools" in (reason or "")


@pytest.mark.asyncio
async def test_degradation_reachable_after_prior_success():
    """审计 H1 回归锁：曾成功过的 server 挂掉后必须仍能降级（attempt
    不得被 last_success 锚定归零——否则退避钉死 500ms、旧代工具永久可见）。"""
    mock = AsyncMock(return_value=[
        {"name": "read", "description": "v1", "inputSchema": {}},
    ])
    with _patch_list_tools(mock):
        await sup.sync_server("fs")
    st = sup._state("fs")
    st.last_success_ts -= 600  # 拨旧：成功发生在 10 分钟前
    err = AsyncMock(side_effect=RuntimeError("connection refused"))
    with _patch_list_tools(err):
        for _ in range(sup.MAX_ATTEMPTS):
            await sup.sync_server("fs", force=True)
    assert st.degraded is True
    assert sup.get_tool_entry("mcp__fs__read") is None
    # 自愈 = 显式 invalidate（改配置/改绑定）
    st.degraded = False
    with _patch_list_tools(mock):
        await sup.invalidate("fs")
    assert sup.get_tool_entry("mcp__fs__read") is not None


@pytest.mark.asyncio
async def test_forget_removes_tools_and_state():
    """审计 M1 回归锁：删除 server 后幽灵工具必须立即不可见。"""
    mock = AsyncMock(return_value=[
        {"name": "read", "description": "", "inputSchema": {}},
    ])
    with _patch_list_tools(mock):
        await sup.sync_server("fs")
    assert sup.get_tool_entry("mcp__fs__read") is not None
    await sup.forget("fs")
    assert sup.get_tool_entry("mcp__fs__read") is None
    assert sup.server_tool_names("fs") == []
    assert sup._states.get("fs") is None
