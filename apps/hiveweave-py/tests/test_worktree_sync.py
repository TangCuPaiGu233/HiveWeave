"""git_worktree_sync（MAIN→worktree 同步完整版）— 服务核心 + 接线自检。

布局构造原则同 test_conflict_predict.py: 真实 git repo +
GitWorktreeService.create 建 worktree, 双方分叉后断言 sync 行为。

覆盖:
- up-to-date 幂等 no-op（merged=False, reason=up_to_date, HEAD 不动）
- 干净落后 → 合并成功（new_head 前移、behind_before、main 提交进入祖先）
- untracked 冲突 → 隔离区搬移 + 回执清单（merge 本体不被 untracked 中止）
- merge-tree 可预判的内容冲突 → 左移拒绝, HEAD 不动、无部分合并状态
- dirty worktree → 自动 pre-merge-checkpoint（merge 方向同一约定）, 不丢改动
- 接线自检: TOOL_PARAM_SCHEMAS / 权限映射（TOOL_CAPABILITY + allowlist）/
  doom_loop 与 quiet-cap 与 git_worktree_merge 同口径 / 派单提示文案
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from hiveweave.services.git_worktree import GitWorktreeService
from hiveweave.services.git_worktree.conflict_predict import (
    predict_merge_conflicts,
)
from hiveweave.services.git_worktree.service_sync import sync_main_into_worktree


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (proc.stdout or "").strip()


def _git_version_ok() -> bool:
    """merge-tree --write-tree 需要 Git >= 2.38（冲突左移门依赖）。"""
    try:
        out = subprocess.run(
            ["git", "--version"], capture_output=True, text=True,
        ).stdout
    except OSError:
        return False
    m = re.search(r"(\d+)\.(\d+)", out or "")
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2))) >= (2, 38)


pytestmark = pytest.mark.skipif(
    not _git_version_ok(), reason="git merge-tree --write-tree requires >= 2.38",
)


@pytest.fixture(autouse=True)
def _reset_supported_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """进程级 merge-tree 支持缓存必须在测试间复位(防跨测试污染)。"""
    monkeypatch.setattr(
        "hiveweave.services.git_worktree.conflict_predict._merge_tree_supported",
        None,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@hiveweave.local")
    _git(repo, "config", "user.name", "HiveWeave Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


async def _make_worktree(repo: Path, sid: str = "A007") -> Path:
    gwt = GitWorktreeService()
    result = await gwt.create(str(repo), sid, "worktree-sync测试")
    assert result["success"] is True, result
    return Path(result["path"])


def _head(wt: Path) -> str:
    return _git(wt, "rev-parse", "--short", "HEAD")


def _in_merge_state(wt: Path) -> bool:
    return (wt / ".git" / "MERGE_HEAD").exists() or (
        _git(wt, "status", "--porcelain").find("UU") >= 0
    )


# ── 服务核心 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_up_to_date_noop(git_repo: Path) -> None:
    """behind==0 → merged=False / reason=up_to_date, HEAD 不动。"""
    wt = await _make_worktree(git_repo)
    head_before = _head(wt)

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is True
    assert result["merged"] is False
    assert result["reason"] == "up_to_date"
    assert result["behind_before"] == 0
    assert result["new_head"] == head_before
    assert result["quarantined"] == []
    assert result["conflicts"] == []
    assert _head(wt) == head_before


@pytest.mark.asyncio
async def test_sync_merges_main_into_clean_worktree(git_repo: Path) -> None:
    """干净落后 → 合并成功: new_head 前移, main 提交成为祖先, behind 报数。"""
    wt = await _make_worktree(git_repo)
    (wt / "branch-only.txt").write_text("b\n", encoding="utf-8")
    _git(wt, "add", "branch-only.txt")
    _git(wt, "commit", "-m", "branch side")
    head_before = _head(wt)
    (git_repo / "main-only.txt").write_text("m\n", encoding="utf-8")
    _git(git_repo, "add", "main-only.txt")
    _git(git_repo, "commit", "-m", "main side")
    main_head = _git(git_repo, "rev-parse", "--short", "main")

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is True, result
    assert result["merged"] is True
    assert result["behind_before"] == 1
    assert result["conflicts"] == []
    assert result["quarantined"] == []
    assert result["new_head"] == _head(wt)
    assert result["new_head"] != head_before
    # main 的新提交已进入 worktree HEAD 祖先
    _git(wt, "merge-base", "--is-ancestor", main_head, "HEAD")
    assert (wt / "main-only.txt").read_text(encoding="utf-8") == "m\n"
    # 分叉（worktree 有自己的提交）→ 是真 merge 而非 fast-forward 也不强求;
    # 这里双方都有新提交, git 产生 merge commit, 工作区干净。
    assert _git(wt, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_sync_pure_behind_worktree_lands_main_commits(git_repo: Path) -> None:
    """worktree 无自有提交（纯落后）→ sync 后 main 新提交进入 HEAD 祖先。

    注：不断言 fast-forward —— gwt.create 会在 worktree 物化平台产物
    （.hiveweave/shared/.keep.md），sync 的自动 checkpoint 会提交它，
    worktree 因此自带一个提交，git 走真 merge。纯 FF 在真实 create 布局
    中不可达。
    """
    wt = await _make_worktree(git_repo)
    (git_repo / "main-only.txt").write_text("m\n", encoding="utf-8")
    _git(git_repo, "add", "main-only.txt")
    _git(git_repo, "commit", "-m", "main side")
    main_head = _git(git_repo, "rev-parse", "--short", "main")

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is True and result["merged"] is True
    assert result["behind_before"] == 1
    _git(wt, "merge-base", "--is-ancestor", main_head, "HEAD")
    assert (wt / "main-only.txt").read_text(encoding="utf-8") == "m\n"


@pytest.mark.asyncio
async def test_sync_quarantines_untracked_collision(git_repo: Path) -> None:
    """MAIN 将覆写的 untracked 文件 → 搬进隔离区, merge 不被 untracked 中止。"""
    wt = await _make_worktree(git_repo)
    (git_repo / "new.txt").write_text("main version\n", encoding="utf-8")
    _git(git_repo, "add", "new.txt")
    _git(git_repo, "commit", "-m", "main adds new.txt")
    # worktree 侧同路径 untracked 文件（未被 git 跟踪）
    (wt / "new.txt").write_text("local draft\n", encoding="utf-8")

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is True, result
    assert result["merged"] is True, "隔离后 merge 应正常完成"
    assert len(result["quarantined"]) == 1
    event = result["quarantined"][0]
    assert event["files"] == ["new.txt"]
    quarantine_copy = Path(event["dest"]) / "new.txt"
    assert quarantine_copy.is_file(), "隔离区应有可恢复副本"
    assert quarantine_copy.read_text(encoding="utf-8") == "local draft\n"
    # worktree 里的 new.txt 现在是 MAIN 版本（tracked）
    assert (wt / "new.txt").read_text(encoding="utf-8") == "main version\n"
    assert _git(wt, "ls-files", "new.txt") == "new.txt"
    assert not _in_merge_state(wt)


@pytest.mark.asyncio
async def test_sync_rejects_predicted_conflict_without_side_effects(
    git_repo: Path,
) -> None:
    """双方改同一文件（已提交）→ 预演拒绝: HEAD 不动、无部分合并状态。"""
    wt = await _make_worktree(git_repo)
    (wt / "file.txt").write_text("branch version\n", encoding="utf-8")
    _git(wt, "add", "file.txt")
    _git(wt, "commit", "-m", "branch change")
    head_before = _head(wt)
    (git_repo / "file.txt").write_text("main version\n", encoding="utf-8")
    _git(git_repo, "add", "file.txt")
    _git(git_repo, "commit", "-m", "main change")
    # 平台物化文件（.hiveweave/shared/.keep.md 等）是 create 自带的既有
    # 状态 —— 「无部分状态」的口径 = sync 前后 status 完全一致。
    status_before = _git(wt, "status", "--porcelain")

    # 前置 sanity: 预演器确实认为冲突（fail-closed 判据来自同一实现）
    pred = await predict_merge_conflicts(str(wt))
    assert pred.status == "conflict"

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is False
    assert result["merged"] is False
    assert result["reason"] == "merge_conflict_predicted"
    assert "file.txt" in result["conflicts"]
    assert result["quarantined"] == []
    # 无部分状态: HEAD 不动、无 MERGE_HEAD、工作区与 sync 前完全一致
    assert _head(wt) == head_before
    assert not _in_merge_state(wt)
    assert _git(wt, "status", "--porcelain") == status_before
    assert (wt / "file.txt").read_text(encoding="utf-8") == "branch version\n"


@pytest.mark.asyncio
async def test_sync_dirty_worktree_auto_checkpoints_like_merge_direction(
    git_repo: Path,
) -> None:
    """dirty worktree → 自动 pre-merge-checkpoint（merge 方向同一约定），
    本地未提交改动不丢，随后 MAIN 提交照常合入。"""
    wt = await _make_worktree(git_repo)
    (wt / "wip.txt").write_text("draft v1\n", encoding="utf-8")
    _git(wt, "add", "wip.txt")
    _git(wt, "commit", "-m", "base for wip")
    # 未提交改动（tracked 文件修改 + 新 untracked 文件各一）
    (wt / "wip.txt").write_text("draft v2\n", encoding="utf-8")
    (wt / "notes.txt").write_text("scratch\n", encoding="utf-8")
    head_before = _head(wt)
    # MAIN 前进（改不同文件, 无内容冲突）
    (git_repo / "main-only.txt").write_text("m\n", encoding="utf-8")
    _git(git_repo, "add", "main-only.txt")
    _git(git_repo, "commit", "-m", "main side")

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is True, result
    assert result["merged"] is True
    # checkpoint 提交发生了（HEAD 动了）
    assert _head(wt) != head_before
    log = _git(wt, "log", "--format=%s", "-5")
    assert "pre-merge-checkpoint" in log
    # 未提交改动被保存（不丢）: 合并后仍是脏 → 但内容在
    status = _git(wt, "status", "--porcelain")
    # dirty 约定 = add -A 后提交, 所以合并后工作区应是干净的, 改动已入库
    assert status == "", f"改动应已被 checkpoint 收编, 实际 status: {status!r}"
    assert (wt / "wip.txt").read_text(encoding="utf-8") == "draft v2\n"
    assert (wt / "notes.txt").read_text(encoding="utf-8") == "scratch\n"
    assert (wt / "main-only.txt").exists()


@pytest.mark.asyncio
async def test_sync_missing_worktree_errors(git_repo: Path) -> None:
    """无 worktree 的 short_id → 结构化错误, 不抛异常。"""
    result = await sync_main_into_worktree(str(git_repo), "A999")
    assert result["success"] is False
    assert result["reason"] == "no_worktree"
    assert result["merged"] is False


# ── 接线自检（5+1 全套）────────────────────────────────────


def test_wiring_tool_param_schemas() -> None:
    """TOOL_PARAM_SCHEMAS 有条目, 描述写清护栏语义（LLM 唯一主源）。"""
    from hiveweave.tools.executor import TOOL_PARAM_SCHEMAS

    entry = TOOL_PARAM_SCHEMAS.get("git_worktree_sync")
    assert entry is not None, "executor.TOOL_PARAM_SCHEMAS 缺 git_worktree_sync"
    desc = entry.get("description", "")
    assert "MAIN" in desc and "worktree" in desc
    assert "quarantine" in desc.lower()
    assert "reject" in desc.lower()
    assert entry.get("required") == []


def test_wiring_policy_capability() -> None:
    """policy.TOOL_CAPABILITY: SOURCE_WRITE|MERGE —— executor（无 MERGE）
    是派单提示的主要受众, 必须 pass 硬门; CEO 走 MERGE 兜底; HR 硬拒。"""
    from hiveweave.services.policy import TOOL_CAPABILITY, tool_hard_deny

    required = TOOL_CAPABILITY.get("git_worktree_sync")
    assert required is not None, "policy.TOOL_CAPABILITY 缺 git_worktree_sync"

    executor_agent = {"permission_type": "executor", "role": "dev"}
    assert tool_hard_deny(executor_agent, "git_worktree_sync") is None
    coordinator_agent = {"permission_type": "coordinator", "role": "lead"}
    assert tool_hard_deny(coordinator_agent, "git_worktree_sync") is None
    ceo_agent = {"permission_type": "ceo", "role": "CEO"}
    assert tool_hard_deny(ceo_agent, "git_worktree_sync") is None
    hr_agent = {"permission_type": "hr", "role": "HR"}
    assert tool_hard_deny(hr_agent, "git_worktree_sync") is not None


def test_wiring_permission_allowlists() -> None:
    """permission.py allowlist: executor（READONLY_TOOLS 基座）与中层
    （COORDINATOR_BUILDER_TOOLS）可见; 不进 COORDINATOR_ONLY（否则 executor
    被逐出）。"""
    from hiveweave.services.permission import (
        ALL_TOOLS,
        COORDINATOR_BUILDER_TOOLS,
        COORDINATOR_ONLY_TOOLS,
        READONLY_TOOLS,
    )

    assert "git_worktree_sync" in READONLY_TOOLS
    assert "git_worktree_sync" in COORDINATOR_BUILDER_TOOLS
    assert "git_worktree_sync" not in COORDINATOR_ONLY_TOOLS
    assert "git_worktree_sync" in ALL_TOOLS


def test_wiring_registry_and_misc_tools() -> None:
    """@tool 注册成功（registry 即执行入口）; tools/__init__ 聚合 import
    misc_tools 后可见。"""
    import hiveweave.tools  # noqa: F401 — 触发 misc_tools 注册
    from hiveweave.tools.base import get_tool_def

    td = get_tool_def("git_worktree_sync")
    assert td is not None, "git_worktree_sync 未注册进 _TOOL_REGISTRY"
    schema = td.to_llm_schema()
    assert "shortId" in schema["properties"]


def test_wiring_doom_loop_and_quiet_cap_mirror_merge() -> None:
    """doom_loop / quiet-cap 与 git_worktree_merge 同口径（merge 不在两张
    表里 → sync 也不进表, 走副作用默认 3 次 / zombie 静默兜底）。"""
    from hiveweave.llm.streamer.doom_loop import (
        DOOM_LOOP_READONLY_TOOLS,
        DOOM_LOOP_TOOL_LIMITS,
        doom_loop_limit,
    )
    from hiveweave.services.game_time import _tool_quiet_cap_ms

    assert "git_worktree_merge" not in DOOM_LOOP_READONLY_TOOLS
    assert "git_worktree_merge" not in DOOM_LOOP_TOOL_LIMITS
    assert "git_worktree_sync" not in DOOM_LOOP_READONLY_TOOLS
    assert "git_worktree_sync" not in DOOM_LOOP_TOOL_LIMITS
    assert doom_loop_limit("git_worktree_sync") == doom_loop_limit(
        "git_worktree_merge"
    )
    assert _tool_quiet_cap_ms("git_worktree_sync") == _tool_quiet_cap_ms(
        "git_worktree_merge"
    )


def test_wiring_dispatch_notice_mentions_sync() -> None:
    """派单 [WORKTREE BEHIND MAIN] 文案已追加 git_worktree_sync 指引。"""
    src_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "hiveweave" / "services" / "dispatch.py"
    )
    src = src_path.read_text(encoding="utf-8")
    anchor = src.find("[WORKTREE BEHIND MAIN]")
    assert anchor > 0, "dispatch.py 找不到 [WORKTREE BEHIND MAIN] 文案"
    tail = src[anchor:anchor + 800]
    assert "git_worktree_sync" in tail


# ── 审计修复回归（P1-1 越权门 / P1-2 二道预演 / P2-1 并发锁 /
#    P2-2 部分隔离回滚）────────────────────────────────────


def _uniq_sid() -> str:
    """A+4位数字（resolve_agent 的 short_id 正则限 2-4 位数字，5 位不匹配）。"""
    import random

    return f"A9{random.randint(100, 999)}"


@pytest.fixture
async def tool_env(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """工具级测试环境：真实 OrgService 种子（meta workspace 打桩到 git_repo），
    _get_worktree_context 打桩，InboxService.send_message 录制。"""
    import uuid as _uuid

    from hiveweave.db import project as project_db
    from hiveweave.services.agent_router import agent_router
    from hiveweave.services.org import OrgService

    pid = f"sync-audit-{_uuid.uuid4().hex[:8]}"
    ws = str(git_repo)

    async def fake_ws(p: str):
        return ws if p == pid else None

    monkeypatch.setattr("hiveweave.db.meta.get_project_workspace", fake_ws)

    org = OrgService()
    sids = {_uniq_sid(), _uniq_sid(), _uniq_sid()}
    caller_sid, peer_sid, sub_sid = sorted(sids)
    caller = await org.create_agent(
        {
            "id": f"sync-caller-{_uuid.uuid4().hex[:8]}",
            "project_id": pid,
            "short_id": caller_sid,
            "name": "sync-caller",
            "role": "developer",
            "permission_type": "coordinator",
            "status": "active",
        },
        bootstrap=True,
    )
    peer = await org.create_agent(
        {
            "id": f"sync-peer-{_uuid.uuid4().hex[:8]}",
            "project_id": pid,
            "short_id": peer_sid,
            "name": "sync-peer",
            "role": "developer",
            "permission_type": "executor",
            "status": "active",
        },
        bootstrap=True,
    )
    sub = await org.create_agent(
        {
            "id": f"sync-sub-{_uuid.uuid4().hex[:8]}",
            "project_id": pid,
            "short_id": sub_sid,
            "name": "sync-sub",
            "role": "developer",
            "permission_type": "executor",
            "status": "active",
            "parent_id": caller["id"],
        },
        bootstrap=True,
    )

    sent: list[dict] = []

    async def _fake_send(self, **kwargs):  # noqa: ANN001
        sent.append(kwargs)
        return True

    monkeypatch.setattr(
        "hiveweave.services.inbox.InboxService.send_message", _fake_send
    )

    import hiveweave.tools.misc_tools as misc

    async def _fake_ctx(agent_id: str, ctx=None):
        return ws, caller_sid, pid

    monkeypatch.setattr(misc, "_get_worktree_context", _fake_ctx)

    env = {
        "ws": ws,
        "pid": pid,
        "caller": caller,
        "peer": peer,
        "sub": sub,
        "caller_sid": caller_sid,
        "peer_sid": peer_sid,
        "sub_sid": sub_sid,
        "sent": sent,
    }
    try:
        yield env
    finally:
        agent_router.clear_project(pid)
        async with project_db._ensure_lock:
            conn = project_db._cache.pop(ws, None)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


async def _call_sync_tool(env: dict, short_param: str | None = None):
    import hiveweave.tools.misc_tools as misc

    kwargs = {"shortId": short_param} if short_param else {}
    params = misc.GitWorktreeSyncParams(**kwargs)
    return await misc.git_worktree_sync_tool(
        params, env["caller"]["id"], env["ws"], ctx=None
    )


@pytest.mark.asyncio
async def test_sync_rejects_unrelated_target(tool_env: dict) -> None:
    """P1-1: 无组织父子关系的 target → 拒绝（fail-closed 越权门）。"""
    import hiveweave.services.git_worktree as gwt_pkg

    wt = await _make_worktree(Path(tool_env["ws"]), tool_env["peer_sid"])
    (Path(tool_env["ws"]) / "main-only.txt").write_text("m\n", encoding="utf-8")
    _git(Path(tool_env["ws"]), "add", "main-only.txt")
    _git(Path(tool_env["ws"]), "commit", "-m", "main side")
    head_before = _head(wt)

    result = await _call_sync_tool(tool_env, tool_env["peer_sid"])

    assert result.success is False
    assert result.error is not None
    assert "not your own" in result.error
    assert "direct superior" in result.error
    # 越权调用不得碰同伴的树
    assert _head(wt) == head_before
    assert gwt_pkg  # import 引用保活


@pytest.mark.asyncio
async def test_sync_allows_direct_subordinate(tool_env: dict) -> None:
    """P1-1: 直属 subordinate（caller 是 target 的 parent）→ 放行并完成同步；
    属主收到 [WORKTREE SYNC] 代同步告知（审计备注 E）。"""
    wt = await _make_worktree(Path(tool_env["ws"]), tool_env["sub_sid"])
    (Path(tool_env["ws"]) / "main-only.txt").write_text("m\n", encoding="utf-8")
    _git(Path(tool_env["ws"]), "add", "main-only.txt")
    _git(Path(tool_env["ws"]), "commit", "-m", "main side")

    result = await _call_sync_tool(tool_env, tool_env["sub_sid"])

    assert result.success is True, result.error
    assert result.extra.get("merged") is True
    assert (wt / "main-only.txt").read_text(encoding="utf-8") == "m\n"
    to_ids = [m.get("to_agent_id") for m in tool_env["sent"]]
    assert tool_env["sub"]["id"] in to_ids, "属主必须收到代同步告知"
    owner_msgs = [
        m for m in tool_env["sent"] if m.get("to_agent_id") == tool_env["sub"]["id"]
    ]
    assert any("[WORKTREE SYNC]" in str(m.get("message")) for m in owner_msgs)


@pytest.mark.asyncio
async def test_sync_own_tree_by_default(tool_env: dict) -> None:
    """P1-1: 省略 shortId → 同步自己的树（不触发越权门）。"""
    await _make_worktree(Path(tool_env["ws"]), tool_env["caller_sid"])
    (Path(tool_env["ws"]) / "main-only.txt").write_text("m\n", encoding="utf-8")
    _git(Path(tool_env["ws"]), "add", "main-only.txt")
    _git(Path(tool_env["ws"]), "commit", "-m", "main side")

    result = await _call_sync_tool(tool_env, None)
    assert result.success is True, result.error
    assert result.extra.get("merged") is True


@pytest.mark.asyncio
async def test_owner_gets_quarantine_notice_on_proxy_sync(
    tool_env: dict,
) -> None:
    """审计备注 E: 上级代同步触发隔离时，属主（target agent）必须同时收到
    [WORKTREE SYNC QUARANTINE]，不能只发调用者。"""
    wt = await _make_worktree(Path(tool_env["ws"]), tool_env["sub_sid"])
    (Path(tool_env["ws"]) / "new.txt").write_text("main version\n", encoding="utf-8")
    _git(Path(tool_env["ws"]), "add", "new.txt")
    _git(Path(tool_env["ws"]), "commit", "-m", "main adds new.txt")
    (wt / "new.txt").write_text("local draft\n", encoding="utf-8")

    result = await _call_sync_tool(tool_env, tool_env["sub_sid"])

    assert result.success is True, result.error
    assert result.extra.get("quarantined")
    quarantine_targets = [
        m.get("to_agent_id")
        for m in tool_env["sent"]
        if "[WORKTREE SYNC QUARANTINE]" in str(m.get("message"))
    ]
    assert tool_env["caller"]["id"] in quarantine_targets
    assert tool_env["sub"]["id"] in quarantine_targets, "属主必须同时收到隔离通知"


@pytest.mark.asyncio
async def test_second_preview_gate_catches_dirty_vs_main_conflict(
    git_repo: Path,
) -> None:
    """P1-2: worktree 未提交改动 vs MAIN 同路径修改 —— 第一道预演
    （纯已提交态）看不到，checkpoint 落成提交后第二道拦下；
    回执明示 checkpoint hash 与处方。"""
    wt = await _make_worktree(git_repo)
    (wt / "branch-side.txt").write_text("b\n", encoding="utf-8")
    _git(wt, "add", "branch-side.txt")
    _git(wt, "commit", "-m", "branch side")
    head_before = _head(wt)
    # MAIN 改 README（worktree 已提交侧不碰 README → 第一道 clean）
    (git_repo / "README.md").write_text("main version\n", encoding="utf-8")
    _git(git_repo, "add", "README.md")
    _git(git_repo, "commit", "-m", "main changes README")
    # worktree 对 README 的**未提交**修改 —— 只有 checkpoint 后才可见
    (wt / "README.md").write_text("dirty local version\n", encoding="utf-8")

    result = await sync_main_into_worktree(str(git_repo), "A007")

    assert result["success"] is False
    assert result["merged"] is False
    assert result["reason"] == "merge_conflict_predicted"
    assert "README.md" in result["conflicts"]
    # 第二道门标记 + checkpoint 副作用透明：回执带 hash 与处方
    assert result.get("post_checkpoint") is True
    checkpoint_hash = result.get("checkpoint")
    assert checkpoint_hash and len(checkpoint_hash) >= 7
    assert f"commit {checkpoint_hash}" in result["message"]
    assert "git_worktree_sync again" in result["message"]
    # checkpoint 提交发生（HEAD 前移）但无半吊子合并态，脏改动不丢
    assert _head(wt) != head_before
    assert not _in_merge_state(wt)
    log = _git(wt, "log", "--format=%s", "-3")
    assert "pre-merge-checkpoint" in log
    committed = _git(wt, "show", f"{checkpoint_hash}:README.md")
    assert committed.strip() == "dirty local version"


@pytest.mark.asyncio
async def test_stuck_quarantine_rolls_back_moved_files(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-2: 部分隔离失败（stuck）→ 已搬文件按原路径搬回再拒绝，
    搬回失败的保留隔离区且回执列明；worktree 无部分状态。"""
    import hiveweave.services.git_worktree.service_sync as ss

    wt = await _make_worktree(git_repo)
    (git_repo / "ok.txt").write_text("main ok\n", encoding="utf-8")
    (git_repo / "bad.txt").write_text("main bad\n", encoding="utf-8")
    _git(git_repo, "add", "ok.txt", "bad.txt")
    _git(git_repo, "commit", "-m", "main adds collision files")
    (wt / "ok.txt").write_text("local ok\n", encoding="utf-8")
    (wt / "bad.txt").write_text("local bad\n", encoding="utf-8")
    head_before = _head(wt)

    real_move = ss.shutil.move

    def _flaky_move(src, dst, *a, **kw):
        if str(src).replace("\\", "/").endswith("bad.txt"):
            raise OSError("forced move failure (test)")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr(ss.shutil, "move", _flaky_move)

    result = await sync_main_into_worktree(str(git_repo), "A007")

    monkeypatch.undo()
    assert result["success"] is False
    assert result["merged"] is False
    assert result["reason"] == "untracked_overwrite"
    assert "bad.txt" in result["message"]
    # 已搬的 ok.txt 被搬回原路径；bad.txt 从未离开
    assert (wt / "ok.txt").read_text(encoding="utf-8") == "local ok\n"
    assert (wt / "bad.txt").read_text(encoding="utf-8") == "local bad\n"
    assert result["quarantined"] == [], "全部搬回后不应残留隔离事件"
    assert not (git_repo / ".hiveweave" / "merge-quarantine").exists() or not any(
        (git_repo / ".hiveweave" / "merge-quarantine").iterdir()
    )
    # 拒绝路径无部分状态
    assert _head(wt) == head_before
    assert not _in_merge_state(wt)


@pytest.mark.asyncio
async def test_concurrent_syncs_serialize_on_same_worktree(
    git_repo: Path,
) -> None:
    """P2-1: 同一 worktree 并发 sync → 锁串行化，绝不出现
    merge_failed / index.lock 误报；后到者看到 behind==0 → up_to_date。"""
    import asyncio as _asyncio

    wt = await _make_worktree(git_repo)
    (git_repo / "m1.txt").write_text("1\n", encoding="utf-8")
    _git(git_repo, "add", "m1.txt")
    _git(git_repo, "commit", "-m", "main 1")
    (git_repo / "m2.txt").write_text("2\n", encoding="utf-8")
    _git(git_repo, "add", "m2.txt")
    _git(git_repo, "commit", "-m", "main 2")

    results = await _asyncio.gather(
        sync_main_into_worktree(str(git_repo), "A007"),
        sync_main_into_worktree(str(git_repo), "A007"),
    )

    merged = [r for r in results if r.get("merged") is True]
    noop = [r for r in results if r.get("reason") == "up_to_date"]
    assert len(merged) == 1 and len(noop) == 1, (
        f"expected 1 merged + 1 up_to_date, got: "
        f"{[(r.get('merged'), r.get('reason')) for r in results]}"
    )
    for r in results:
        assert r["success"] is True, r
        assert r.get("reason") != "merge_failed"
    # 合并完整：两个 MAIN 提交都在 worktree HEAD 祖先里
    _git(wt, "merge-base", "--is-ancestor", "main", "HEAD")
    assert (wt / "m1.txt").exists() and (wt / "m2.txt").exists()
    assert not _in_merge_state(wt)
