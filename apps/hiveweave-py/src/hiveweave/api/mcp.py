"""MCP server management endpoints (contract 10 REST wiring).

契约 10: MCP — 服务器配置 CRUD + agent 绑定 + 工具列表查询
- GET    /api/mcp/servers                     列出所有 MCP 服务器
- POST   /api/mcp/servers                     新增或更新（upsert 语义）
- DELETE /api/mcp/servers/{name}              删除
- GET    /api/mcp/servers/{name}/tools        该服务器工具列表（离线/失败 → 503）
- GET    /api/mcp/agents/{agent_id}           查 agent 已绑定的服务器名列表
- POST   /api/mcp/agents/{agent_id}/bind      绑定
- POST   /api/mcp/agents/{agent_id}/unbind    解绑

数据层全部在 ``services/mcp.py``（mcp_service 单例）；本模块只做 HTTP 契约
与错误映射。配置/绑定变更后 best-effort 触发 ``mcp_supervisor`` 的工具表
重同步（懒导入 + getattr 防御 —— supervisor 由并行会话开发，模块或函数
可能尚未落地，缺失时静默跳过）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import structlog

from hiveweave.services.mcp import mcp_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class McpServerUpsert(BaseModel):
    """新增/更新 MCP 服务器请求体（upsert 语义）。"""

    name: str
    transport: Literal["http", "stdio"] = "http"
    command: str = ""
    url: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class McpBindRequest(BaseModel):
    """绑定/解绑请求体。"""

    server: str


def _to_server_out(cfg: dict) -> dict:
    """构造同时含 snake_case 与 camelCase 字段的响应 dict。"""
    out = dict(cfg)
    created = cfg.get("created_at")
    out["createdAt"] = created
    return out


async def _invalidate_server(name: str) -> None:
    """配置变更后 best-effort 触发 supervisor 工具表重同步。

    create_task 派发（审计 M2）：同步 await 会真实拉 tools/list（30s
    超时），UI 请求最坏阻塞 30s×N——派发后台任务立即返回。
    """
    try:
        import asyncio

        from hiveweave.services import mcp_supervisor
    except Exception:
        return  # supervisor 模块未落地，静默跳过
    invalidate = getattr(mcp_supervisor, "invalidate", None)
    if invalidate is None:
        return

    async def _run() -> None:
        try:
            await invalidate(name)
        except Exception as e:
            log.warning(
                "mcp_supervisor_invalidate_failed", server=name, error=str(e)
            )

    asyncio.create_task(_run())


async def _invalidate_agent(agent_id: str) -> None:
    """绑定变更后 best-effort 触发 supervisor 按 agent 重同步（后台派发）。"""
    try:
        import asyncio

        from hiveweave.services import mcp_supervisor
    except Exception:
        return  # supervisor 模块未落地，静默跳过
    invalidate_agent = getattr(mcp_supervisor, "invalidate_agent", None)
    if invalidate_agent is None:
        return

    async def _run() -> None:
        try:
            await invalidate_agent(agent_id)
        except Exception as e:
            log.warning(
                "mcp_supervisor_invalidate_agent_failed",
                agent_id=agent_id,
                error=str(e),
            )

    asyncio.create_task(_run())


@router.get("/servers")
async def list_servers() -> dict:
    """列出所有已配置的 MCP 服务器。"""
    try:
        servers = await mcp_service.list_servers()
    except Exception as e:
        log.error("list_mcp_servers_failed", error=str(e))
        return {"servers": []}
    return {"servers": [_to_server_out(s) for s in servers]}


@router.post("/servers")
async def upsert_server(body: McpServerUpsert) -> dict:
    """新增或更新 MCP 服务器（upsert 语义）。"""
    if "__" in body.name:
        # 公开名形状 mcp__<server>__<raw>：server 含 __ 会跨 server 碰撞
        raise HTTPException(
            status_code=422,
            detail="MCP server name must not contain '__'",
        )
    try:
        await mcp_service.add_server(
            name=body.name,
            transport=body.transport,
            command=body.command,
            url=body.url,
            args=body.args,
            env=body.env,
            enabled=body.enabled,
        )
    except Exception as e:
        log.error("upsert_mcp_server_failed", name=body.name, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save MCP server")
    await _invalidate_server(body.name)
    cfg = await mcp_service.get_server(body.name)
    return {"ok": True, "server": _to_server_out(cfg or {})}


@router.delete("/servers/{name}")
async def delete_server(name: str) -> dict:
    """删除 MCP 服务器配置。"""
    existing = await mcp_service.get_server(name)
    if existing is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    try:
        await mcp_service.remove_server(name)
    except Exception as e:
        log.error("delete_mcp_server_failed", name=name, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to delete MCP server")
    # 删除≠重同步：forget 直接摘工具表与状态，绑定 agent 不再见幽灵工具
    try:
        import asyncio

        from hiveweave.services import mcp_supervisor

        forget = getattr(mcp_supervisor, "forget", None)
        if forget is not None:
            asyncio.create_task(forget(name))
    except Exception as e:
        log.warning("mcp_supervisor_forget_failed", name=name, error=str(e))
    return {"ok": True, "name": name}


@router.get("/servers/{name}/tools")
async def list_server_tools(name: str) -> dict:
    """列出该 MCP 服务器的工具；离线/连接失败 → 503 + reason（不抛栈）。"""
    try:
        tools = await mcp_service.list_tools(name)
    except Exception as e:
        log.warning("list_mcp_server_tools_failed", server=name, error=str(e))
        raise HTTPException(status_code=503, detail={"reason": str(e)})
    return {"server": name, "tools": tools, "count": len(tools)}


@router.get("/agents/{agent_id}")
async def get_agent_mcp(agent_id: str) -> dict:
    """查 agent 已绑定的 MCP 服务器名列表。"""
    try:
        servers = await mcp_service.get_bound_mcp(agent_id)
    except Exception as e:
        log.error("get_agent_mcp_failed", agent_id=agent_id, error=str(e))
        return {"agentId": agent_id, "agent_id": agent_id, "servers": [], "count": 0}
    return {
        "agentId": agent_id,
        "agent_id": agent_id,
        "servers": servers,
        "count": len(servers),
    }


async def _bind_or_unbind(agent_id: str, server: str, *, bind: bool) -> dict:
    """bind/unbind 共用路径：失败 → 400（数据层返回 {ok: False, error}）。"""
    try:
        result = (
            await mcp_service.bind_mcp(agent_id, server)
            if bind
            else await mcp_service.unbind_mcp(agent_id, server)
        )
    except Exception as e:
        log.error(
            "mcp_bind_op_failed",
            agent_id=agent_id,
            server=server,
            action="bind" if bind else "unbind",
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to update MCP binding")
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "MCP bind operation failed"),
        )
    await _invalidate_agent(agent_id)
    return {
        "ok": True,
        "agentId": agent_id,
        "agent_id": agent_id,
        "server": server,
        "action": "bind" if bind else "unbind",
    }


@router.post("/agents/{agent_id}/bind")
async def bind_agent_mcp(agent_id: str, body: McpBindRequest) -> dict:
    """绑定 MCP 服务器到 agent。"""
    return await _bind_or_unbind(agent_id, body.server, bind=True)


@router.post("/agents/{agent_id}/unbind")
async def unbind_agent_mcp(agent_id: str, body: McpBindRequest) -> dict:
    """解绑 agent 的 MCP 服务器。"""
    return await _bind_or_unbind(agent_id, body.server, bind=False)
