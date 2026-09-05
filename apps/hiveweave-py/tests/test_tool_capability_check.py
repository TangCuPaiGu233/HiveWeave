"""件2（42 轮报告 P1）：TOOL_CAPABILITY 全覆盖启动断言。

背景：tool_hard_deny 对未映射工具放行（.get → None），start_dev_server
曾因此绕过 CEO bash 硬门（08-13 实锤）。assert_all_tools_mapped() 保证
每个注册工具「有映射或在显式豁免集」，缺一即 fail-loud。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from hiveweave.services.policy import TOOL_CAPABILITY
from hiveweave.services.tool_capability_check import (
    EXEMPT_TOOLS,
    ToolCapabilityMappingError,
    assert_all_tools_mapped,
)


class _FakeParams(BaseModel):
    """Module-level: @tool decorator resolves type hints at module scope."""

    x: int = 0


def _register_fake(name: str):
    import hiveweave.tools  # noqa: F401 — 填充注册表
    from hiveweave.tools.base import _TOOL_REGISTRY, tool

    @tool(name, "temp fixture: tool capability mapping assertion test")
    def _zz_fake(params: _FakeParams, agent_id: str, workspace: str):
        return None

    return _TOOL_REGISTRY


def test_current_registry_fully_mapped_or_exempt():
    """当前注册表全部工具有判定（映射或豁免）—— 启动断言不抛。"""
    assert_all_tools_mapped()  # must not raise


def test_look_at_image_exemption_is_locked_by_test():
    """豁免集佐证：look_at_image 必须留映射外（HR 无 BROWSE 也要能看图）。

    tests/test_look_at_image.py::test_look_at_image_not_bound_to_browse_capability
    是同一设计决策的回归锁。
    """
    assert "look_at_image" in EXEMPT_TOOLS
    assert "look_at_image" not in TOOL_CAPABILITY


def test_execution_channels_capability_gated_p1():
    """审计 P1：python_script/run_smoke 是执行通道，必须映射、不得豁免。

    python_script native 路径不经 command_guard，留在映射外即可用
    `script="subprocess.run(['icacls',...])"` 一行绕过 bash 硬门。
    """
    from hiveweave.services.policy import tool_hard_deny

    assert "python_script" in TOOL_CAPABILITY
    assert "run_smoke" in TOOL_CAPABILITY
    assert "python_script" not in EXEMPT_TOOLS
    assert "run_smoke" not in EXEMPT_TOOLS

    hr = {"role": "hr", "permission_type": "hr", "name": "知远"}
    ceo = {"role": "ceo", "permission_type": "readonly", "name": "归零"}
    executor = {"role": "dev", "permission_type": "executor", "name": "白鹭"}
    qa = {"role": "test_engineer", "permission_type": "qa", "name": "Nova"}
    # HR（无 BASH_SHELL）python_script 撞门；executor 畅通
    assert tool_hard_deny(hr, "python_script") is not None
    assert tool_hard_deny(executor, "python_script") is None
    # CEO（无 TEST_RUN）run_smoke 撞门；executor/QA 畅通
    assert tool_hard_deny(ceo, "run_smoke") is not None
    assert tool_hard_deny(executor, "run_smoke") is None
    assert tool_hard_deny(qa, "run_smoke") is None


def test_unmapped_fake_tool_fails_loud_with_name():
    """临时注册未映射假工具 → 断言抛出且报文列出工具名。"""
    registry = _register_fake("zz_fake_unmapped_tool")
    try:
        with pytest.raises(ToolCapabilityMappingError) as ei:
            assert_all_tools_mapped()
    finally:
        registry.pop("zz_fake_unmapped_tool", None)
    msg = str(ei.value)
    assert "zz_fake_unmapped_tool" in msg
    assert "TOOL_CAPABILITY" in msg


def test_exempt_fake_tool_does_not_raise():
    """同一假工具进了豁免集 → 不抛（豁免集内不抛的判定路径）。"""
    from hiveweave.services import tool_capability_check as tcc

    registry = _register_fake("zz_fake_exempt_tool")
    try:
        with pytest.raises(ToolCapabilityMappingError):
            assert_all_tools_mapped()  # 未豁免 → 应抛
        orig = tcc.EXEMPT_TOOLS
        tcc.EXEMPT_TOOLS = frozenset({*orig, "zz_fake_exempt_tool"})
        try:
            assert_all_tools_mapped()  # 豁免后不抛
        finally:
            tcc.EXEMPT_TOOLS = orig
    finally:
        registry.pop("zz_fake_exempt_tool", None)
