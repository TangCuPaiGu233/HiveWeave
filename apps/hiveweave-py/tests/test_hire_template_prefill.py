"""件2（templateId 死参数修复 2026-09-05）单元测试：hire_agent 模板预填。

此前 ``template_id = params.template_id`` 赋值后全文件无消费；预填映射与
已删除的前端 AddAgentDialog 一致：role←tpl.role、goal←tpl.vibe 或
description、backstory←tpl.prompt_body；只填空位，显式传值不覆盖。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hiveweave.tools.org_tools import HireAgentParams, hire_agent_tool

_CEO = {
    "id": "ceo-uuid-1",
    "short_id": "CEO1",
    "name": "老板",
    "role": "CEO",
    "status": "active",
    "permission_type": "ceo",
    "parent_id": "",
}

_TEMPLATE = {
    "id": "tpl-1",
    "name": "前端专家",
    "role": "前端工程师（模板位）",
    "vibe": "严谨的 UI 手艺人",
    "description": "描述兜底文案",
    "prompt_body": "你是资深前端，重视设计还原度与可访问性。",
    "discipline_suite": "self-review",
}


def _make_ctx(template: dict | None) -> MagicMock:
    ctx = MagicMock()
    ctx.org = MagicMock()
    ctx.org.list_agents = AsyncMock(return_value=[dict(_CEO)])
    ctx.org.get_agent_by_role = AsyncMock(return_value=dict(_CEO))
    ctx.org.create_agent = AsyncMock(
        return_value={"id": "new-uuid-1", "short_id": "a1b2"}
    )
    ctx.org.update_agent = AsyncMock(return_value=True)
    ctx.skills = MagicMock()
    ctx.templates = MagicMock()
    ctx.templates.get = AsyncMock(return_value=template)
    return ctx


@pytest.fixture
def _hire_env():
    """屏蔽 hire_agent_tool 的真实 DB 依赖（project 解析 / 模型层 / 语言列）。"""
    with patch(
        "hiveweave.tools.org_tools.get_project_id",
        AsyncMock(return_value="proj-1"),
    ), patch(
        "hiveweave.services.model.ModelService"
    ) as ms_cls, patch(
        "hiveweave.db.project.get_project_db_by_project_id",
        AsyncMock(side_effect=RuntimeError("no project db in unit test")),
    ):
        ms_cls.return_value.resolve_model = AsyncMock(return_value=None)
        yield


def _params(**kw) -> HireAgentParams:
    base = dict(
        name="潮汐",
        role="测试工程师",
        system_prompt=None,
        goal=None,
        permission_type="qa",
        parent_agent_id="ceo-uuid-1",
        skills=None,
        template_id=None,
    )
    base.update(kw)
    return HireAgentParams(**base)


@pytest.mark.asyncio
async def test_template_prefills_empty_slots(_hire_env):
    ctx = _make_ctx(dict(_TEMPLATE))
    result = await hire_agent_tool(
        _params(template_id="tpl-1"), "hr-uuid", "/ws", ctx
    )
    assert result.success is True, result.error
    attrs = ctx.org.create_agent.call_args[0][0]
    # 空位被填：goal ← vibe、backstory ← prompt_body
    assert attrs["goal"] == "严谨的 UI 手艺人"
    assert attrs["backstory"] == _TEMPLATE["prompt_body"]
    # 模板表无 skills 列 → skills 不做模板预填
    assert attrs["skills"] == []


@pytest.mark.asyncio
async def test_template_falls_back_to_description_when_vibe_empty(_hire_env):
    tpl = dict(_TEMPLATE, vibe="")
    ctx = _make_ctx(tpl)
    result = await hire_agent_tool(
        _params(template_id="tpl-1"), "hr-uuid", "/ws", ctx
    )
    assert result.success is True, result.error
    attrs = ctx.org.create_agent.call_args[0][0]
    assert attrs["goal"] == "描述兜底文案"


@pytest.mark.asyncio
async def test_explicit_values_not_overwritten(_hire_env):
    ctx = _make_ctx(dict(_TEMPLATE))
    result = await hire_agent_tool(
        _params(
            template_id="tpl-1",
            goal="显式目标优先",
            system_prompt="显式背景优先",
        ),
        "hr-uuid",
        "/ws",
        ctx,
    )
    assert result.success is True, result.error
    attrs = ctx.org.create_agent.call_args[0][0]
    assert attrs["goal"] == "显式目标优先"
    assert attrs["backstory"] == "显式背景优先"


@pytest.mark.asyncio
async def test_unknown_template_id_errors(_hire_env):
    ctx = _make_ctx(None)
    result = await hire_agent_tool(
        _params(template_id="tpl-nope"), "hr-uuid", "/ws", ctx
    )
    assert result.success is not True
    assert "模板不存在" in str(result.error or "")
    assert "list_agent_templates" in str(result.error or "")
    ctx.org.create_agent.assert_not_called()


@pytest.mark.asyncio
async def test_no_template_id_behavior_unchanged(_hire_env):
    ctx = _make_ctx(dict(_TEMPLATE))
    result = await hire_agent_tool(_params(), "hr-uuid", "/ws", ctx)
    assert result.success is True, result.error
    ctx.templates.get.assert_not_called()
    attrs = ctx.org.create_agent.call_args[0][0]
    # 无模板 → role-based 兜底 goal、空 backstory（既有行为回归）
    assert attrs["goal"] == "Execute 测试工程师 responsibilities."
    assert attrs["backstory"] == ""


# ── P1-4：role 可省 —— 模板预填须先于 role 校验 ────────


@pytest.mark.asyncio
async def test_template_supplies_role_goal_backstory_all(_hire_env):
    """只传 templateId 不传 role/goal/backstory → 三者全部从模板预填成功。"""
    ctx = _make_ctx(dict(_TEMPLATE))
    result = await hire_agent_tool(
        _params(role=None, template_id="tpl-1"), "hr-uuid", "/ws", ctx
    )
    assert result.success is True, result.error
    attrs = ctx.org.create_agent.call_args[0][0]
    assert attrs["role"] == _TEMPLATE["role"]
    assert attrs["goal"] == _TEMPLATE["vibe"]
    assert attrs["backstory"] == _TEMPLATE["prompt_body"]


@pytest.mark.asyncio
async def test_missing_role_without_template_role_errors(_hire_env):
    """模板也没有 role（且调用方没传）→ 才报 role 缺失。"""
    tpl = dict(_TEMPLATE, role="")
    ctx = _make_ctx(tpl)
    result = await hire_agent_tool(
        _params(role=None, template_id="tpl-1"), "hr-uuid", "/ws", ctx
    )
    assert result.success is not True
    assert "role" in str(result.error or "")
    ctx.org.create_agent.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_role_not_overwritten_by_template(_hire_env):
    """显式传了 role → 模板 role 不覆盖（P1-4 上移后语义保持）。"""
    ctx = _make_ctx(dict(_TEMPLATE))
    result = await hire_agent_tool(
        _params(template_id="tpl-1"), "hr-uuid", "/ws", ctx
    )
    assert result.success is True, result.error
    attrs = ctx.org.create_agent.call_args[0][0]
    assert attrs["role"] == "测试工程师"
    # goal/backstory 仍从模板预填（空位照填）
    assert attrs["goal"] == _TEMPLATE["vibe"]
    assert attrs["backstory"] == _TEMPLATE["prompt_body"]
