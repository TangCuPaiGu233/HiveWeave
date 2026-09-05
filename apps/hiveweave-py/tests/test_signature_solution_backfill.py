"""件1（42 轮 R7 恶化项处置 2026-09-05）单元测试：失败签名解法回填。

背景：hint 18/18 无效 —— 条目只有错误原文没有解法，且常由失败者自己刚写。
修复 = backfill_solution 写回 + executor「失败记 pending → 同 agent 同工具
首次成功即回填成功参数摘要」+ _signature_has_solution 认可解法行。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hiveweave.services import failure_signature as fs
from hiveweave.tools import executor as exec_mod

_ERROR = "Error: Command blocked: [unattended mode] something long enough"


class _FakeSharedSpace:
    """内存版项目共享空间：模拟 MemoryService 的读 + 固定写入方 upsert。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def get_project_memories(self, project_id: str) -> list[dict]:
        return [dict(r) for r in self.rows]

    async def save_memory(
        self,
        *,
        agent_id: str,
        project_id: str,
        scope: str,
        content: str,
        type: str = "fact",
        module_id: str | None = None,
        source_agent_id: str | None = None,
        metadata: dict | None = None,
        **_: object,
    ) -> str:
        for r in self.rows:
            if (
                r.get("agent_id") == agent_id
                and r.get("scope") == scope
                and r.get("module_id") == module_id
            ):
                r.update(
                    content=content,
                    type=type,
                    source_agent_id=source_agent_id,
                    metadata=metadata,
                )
                return r["id"]
        mid = f"mem-{len(self.rows) + 1}"
        self.rows.append(
            {
                "id": mid,
                "agent_id": agent_id,
                "project_id": project_id,
                "scope": scope,
                "module_id": module_id,
                "type": type,
                "content": content,
                "source_agent_id": source_agent_id,
                "metadata": metadata or {},
            }
        )
        return mid


@pytest.fixture
def space():
    fake = _FakeSharedSpace()
    with patch(
        "hiveweave.services.memory.MemoryService", return_value=fake
    ):
        yield fake


@pytest.fixture
def clear_pending():
    exec_mod._PENDING_SOLUTIONS.clear()
    yield
    exec_mod._PENDING_SOLUTIONS.clear()


def _entry_content(sig: str, source: str = "agent-A") -> str:
    return (
        f"[失败签名] tool=bash | {sig}\n"
        f"根因提示: 见错误原文\n"
        f"原文尾: {sig[-40:]}\n"
        f"首个撞到的 Agent: {source}"
    )


# ── 失败 → 同 agent 同工具成功 → 条目含解法行 + hint 恢复 ──


@pytest.mark.asyncio
async def test_fail_then_success_backfills_solution_and_restores_hint(
    space, clear_pending
):
    sig = fs.signature_of(_ERROR)
    assert sig

    with patch(
        "hiveweave.db.meta.get_agent_project_id", AsyncMock(return_value="proj")
    ):
        # 1) 失败：写入签名条目 + 记 pending
        fail_result = {"success": False, "output": "", "error": _ERROR}
        await exec_mod._f10_result_hooks(
            fail_result, "bash", {"command": "ls --unix-only"}, "agent-A"
        )
        assert len(space.rows) == 1
        assert (  # pending 已记录
            "agent-A",
            "bash",
        ) in exec_mod._PENDING_SOLUTIONS
        assert not fs._signature_has_solution(space.rows[0]["content"])

        # 2) 同 agent 同工具随后一次成功 → 回填成功参数摘要
        ok_result = {"success": True, "output": "file.txt", "error": None}
        await exec_mod._f10_pending_success_backfill(
            ok_result, "bash", {"command": "pwsh -NoProfile -Command ls"}, "agent-A"
        )
        assert (  # pending 已消费
            "agent-A",
            "bash",
        ) not in exec_mod._PENDING_SOLUTIONS

    content = space.rows[0]["content"]
    assert "已验证解法:" in content
    assert "pwsh -NoProfile -Command ls"[:60] in content
    assert fs._signature_has_solution(content)
    # 解法行插在根因行之后
    lines = content.splitlines()
    assert lines[1].startswith("根因提示:")
    assert lines[2].startswith("已验证解法:")

    # 3) hint 恢复广播：别人撞同一签名能拿到 [shared fix]
    hint = await fs.known_signature_hint("proj", _ERROR, agent_id="agent-B")
    assert hint and "[shared fix]" in hint


@pytest.mark.asyncio
async def test_preexisting_hint_goes_to_other_agent_not_self(space):
    """preexisting 既有语义回归：带解法条目 → hint 给别人，首撞者自指抑制。"""
    sig = fs.signature_of(_ERROR)
    space.rows.append(
        {
            "id": "mem-1",
            "agent_id": fs._SIGNATURE_WRITER,
            "scope": "project",
            "module_id": fs.make_module_id("proj", sig),
            "type": "failure_signature",
            "content": _entry_content(sig) + "\n已验证解法: 改用 pwsh 写法重试",
            "source_agent_id": "agent-A",
            "metadata": {"source_agent_id": "agent-A"},
        }
    )

    with patch(
        "hiveweave.db.meta.get_agent_project_id", AsyncMock(return_value="proj")
    ):
        # 别人（agent-B）撞坑 → 收到 [shared fix]
        result_b = {"success": False, "output": "", "error": _ERROR}
        await exec_mod._f10_result_hooks(
            result_b, "bash", {}, "agent-B"
        )
        assert "[shared fix]" in (result_b.get("error") or "")

        # 首撞者（agent-A）自己再撞 → 自指抑制，无 hint
        result_a = {"success": False, "output": "", "error": _ERROR}
        await exec_mod._f10_result_hooks(
            result_a, "bash", {}, "agent-A"
        )
        assert "[shared fix]" not in (result_a.get("error") or "")


# ── 占位 / 无实质解法不回填 ──


@pytest.mark.asyncio
async def test_placeholder_solution_not_backfilled(space):
    sig = fs.signature_of(_ERROR)
    space.rows.append(
        {
            "id": "mem-1",
            "agent_id": fs._SIGNATURE_WRITER,
            "scope": "project",
            "module_id": fs.make_module_id("proj", sig),
            "type": "failure_signature",
            "content": _entry_content(sig),
            "source_agent_id": "agent-A",
            "metadata": {},
        }
    )
    before = space.rows[0]["content"]

    ok = await fs.backfill_solution(
        sig, "bash", "见错误原文", project_id="proj"
    )
    assert ok is False
    assert space.rows[0]["content"] == before  # 内容未被污染

    # 过短解法同样拒绝
    ok_short = await fs.backfill_solution(sig, "bash", "pwd", project_id="proj")
    assert ok_short is False
    assert space.rows[0]["content"] == before


@pytest.mark.asyncio
async def test_success_without_pending_no_backfill(space, clear_pending):
    """无 pending 的成功不误回填（共享空间零写入）。"""
    ok_result = {"success": True, "output": "done", "error": None}
    await exec_mod._f10_pending_success_backfill(
        ok_result, "bash", {"command": "pwsh -Command ls"}, "agent-X"
    )
    assert space.rows == []


@pytest.mark.asyncio
async def test_backfill_idempotent_when_solution_line_exists(space):
    sig = fs.signature_of(_ERROR)
    solved = _entry_content(sig) + "\n已验证解法: 已有解法保持原样"
    space.rows.append(
        {
            "id": "mem-1",
            "agent_id": fs._SIGNATURE_WRITER,
            "scope": "project",
            "module_id": fs.make_module_id("proj", sig),
            "type": "failure_signature",
            "content": solved,
            "source_agent_id": "agent-A",
            "metadata": {},
        }
    )
    ok = await fs.backfill_solution(
        sig, "bash", "另一个解法不应覆盖", project_id="proj"
    )
    assert ok is True  # 幂等跳过
    assert "已有解法保持原样" in space.rows[0]["content"]
    assert "另一个解法不应覆盖" not in space.rows[0]["content"]


def test_signature_has_solution_recognizes_verified_line():
    base = _entry_content("sig-longer-than-twelve-characters")
    assert not fs._signature_has_solution(base)  # 占位根因 → 无解
    assert fs._signature_has_solution(base + "\n已验证解法: 改用 pwsh 写法")
    # 空解法行不认
    assert not fs._signature_has_solution(base + "\n已验证解法:")


# ── P1-2：敏感值脱敏（键名匹配 + 递归）──────────────────


@pytest.mark.asyncio
async def test_sensitive_args_redacted_before_backfill(space, clear_pending):
    """command/env/authorization 命中键名的值不落原文，只落已隐藏占位。"""
    sig = fs.signature_of(_ERROR)
    with patch(
        "hiveweave.db.meta.get_agent_project_id", AsyncMock(return_value="proj")
    ):
        await exec_mod._f10_result_hooks(
            {"success": False, "output": "", "error": _ERROR},
            "bash",
            {"command": "ls"},
            "agent-A",
        )
        await exec_mod._f10_pending_success_backfill(
            {"success": True, "output": "ok", "error": None},
            "bash",
            {
                "command": "curl -H 'Authorization: Bearer sk-abc123' https://x",
                "env": {"API_TOKEN": "sk-secret-value", "HOME": "/home/u"},
                "headers": {"Authorization": "Bearer sk-leak-me"},
                "path": "a.txt",
            },
            "agent-A",
        )
    content = space.rows[0]["content"]
    assert "已验证解法:" in content  # 回填本身成功
    # 键名命中的值 → 已隐藏占位（env 整个 dict、headers.Authorization 值）
    assert "已隐藏" in content
    # 敏感值原文绝不出现
    assert "sk-secret-value" not in content
    assert "sk-leak-me" not in content
    assert "/home/u" not in content  # env 整值已隐藏，内层兄弟值不外泄
    assert "API_TOKEN" not in content
    # 未命中键名的顶层参数值保留（其余值保留，审计 P1-2 语义）
    assert "a.txt" in content
    assert "curl -H" in content


def test_redact_recurses_nested_containers():
    args = {
        "command": "deploy.sh",
        "config": {
            "api_key": "sk-xyz",
            "nested": [{"PASSWORD": "hunter2"}, {"note": "keep"}],
        },
        "authority": "kept? auth substring matches",
    }
    out = exec_mod._redact_for_shared_solution(args)
    assert out["command"] == "deploy.sh"
    assert out["config"]["api_key"].startswith("<已隐藏")
    assert out["config"]["nested"][0]["PASSWORD"].startswith("<已隐藏")
    assert out["config"]["nested"][1]["note"] == "keep"
    assert out["authority"].startswith("<已隐藏")  # auth 子串命中（保守）


# ── P1-3：空参数解法不回填（防镜子条目换马甲）──────────


@pytest.mark.asyncio
async def test_empty_args_success_does_not_backfill(space, clear_pending):
    """tool_args={} → 解法无信息量，消费 pending 即弃，不回填不重开自指闸。"""
    sig = fs.signature_of(_ERROR)
    with patch(
        "hiveweave.db.meta.get_agent_project_id", AsyncMock(return_value="proj")
    ):
        await exec_mod._f10_result_hooks(
            {"success": False, "output": "", "error": _ERROR},
            "bash",
            {"command": "ls"},
            "agent-A",
        )
        await exec_mod._f10_pending_success_backfill(
            {"success": True, "output": "ok", "error": None}, "bash", {}, "agent-A"
        )
    assert space.rows and "已验证解法:" not in space.rows[0]["content"]
    assert not fs._signature_has_solution(space.rows[0]["content"])
    assert ("agent-A", "bash") not in exec_mod._PENDING_SOLUTIONS  # pending 已消费


@pytest.mark.asyncio
async def test_all_empty_values_do_not_backfill(space, clear_pending):
    """只有空串/None 参数 → 同样视为无实质解法。"""
    with patch(
        "hiveweave.db.meta.get_agent_project_id", AsyncMock(return_value="proj")
    ):
        await exec_mod._f10_result_hooks(
            {"success": False, "output": "", "error": _ERROR},
            "bash",
            {"command": "ls"},
            "agent-A",
        )
        await exec_mod._f10_pending_success_backfill(
            {"success": True, "output": "ok", "error": None},
            "bash",
            {"note": "", "flag": None},
            "agent-A",
        )
    assert space.rows and "已验证解法:" not in space.rows[0]["content"]


# ── 备注①：pending 过期不兑现 ─────────────────────────


@pytest.mark.asyncio
async def test_expired_pending_is_discarded_not_backfilled(space, clear_pending):
    """超 TTL 的 pending 即使随后成功也不兑现（与 TTL 注释一致）。"""
    sig = fs.signature_of(_ERROR)
    space.rows.append(
        {
            "id": "mem-1",
            "agent_id": fs._SIGNATURE_WRITER,
            "scope": "project",
            "module_id": fs.make_module_id("proj", sig),
            "type": "failure_signature",
            "content": _entry_content(sig),
            "source_agent_id": "agent-A",
            "metadata": {},
        }
    )
    exec_mod._PENDING_SOLUTIONS[("agent-A", "bash")] = {
        "project_id": "proj",
        "sig": sig,
        "ts": 0,  # 远古时间戳 → 必然超 TTL
    }
    await exec_mod._f10_pending_success_backfill(
        {"success": True, "output": "ok", "error": None},
        "bash",
        {"command": "pwsh -Command ls"},
        "agent-A",
    )
    assert "已验证解法:" not in space.rows[0]["content"]
    assert ("agent-A", "bash") not in exec_mod._PENDING_SOLUTIONS  # 已消费即弃
