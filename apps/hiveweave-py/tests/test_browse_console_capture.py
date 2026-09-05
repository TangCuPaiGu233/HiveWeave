"""browse console 实文捕获（browse_e2e / visual_check attestation 的 console_errors）。

背景：40 轮诊断瓶颈的根 —— DOM 探针盲飞。页面 JS 抛错（如未声明变量）时
a11y 树仍可能渲染得像模像样，agent 把产品 bug 误读成测试问题。agent-browser
CLI 提供 `console [--clear]` / `errors [--clear]`（JSON 信封已实测核实），这里
验证契约：

1. CLI 支持 → 真实错误计数进 attestation.console_errors + 返回文本带 [console]
   段（≤50 行 / 4KB）。console 流读后清（每条命令增量，读→清窗口=一次 spawn
   间隔，见源码块注释）；errors 流清不掉（CLI 破损）→ 进程内已读清单去重。
2. CLI 不支持 / 抓取失败 / 坏 payload（含键名漂移）→ fail-open：console_errors=0
   但返回文本明确声明「未捕获」，绝不撒谎称页面干净，也绝不拖垮 browse 本身。
3. console/errors/close 命令自身不触发捕获（避免吃掉 agent 正要读的缓冲）；
   close/restart 重建会话时 pageerror 去重状态作废。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import hiveweave.tools.browse_tools as bt
from hiveweave.tools.browse_tools import (
    CONSOLE_CAPTURE_UNAVAILABLE_NOTE,
    CONSOLE_CLEAR_FAILED_NOTE,
    AssertVisualParams,
    BrowseParams,
    assert_visual_tool,
    browse_tool,
)


def _envelope(data: dict) -> str:
    return json.dumps({"success": True, "data": data, "error": None})


_CONSOLE_ONE_ERROR = _envelope(
    {
        "messages": [
            {
                "args": [{"type": "string", "value": "foliageMat is not defined"}],
                "text": "Uncaught ReferenceError: foliageMat is not defined",
                "type": "error",
            },
            {"text": "deprecated API used", "type": "warning"},
        ]
    }
)
_ERRORS_ONE_UNCAUGHT = _envelope(
    {"errors": [{"column": 1, "line": 0, "text": "Error: uncaught probe", "url": None}]}
)
_CONSOLE_EMPTY = _envelope({"messages": []})
_ERRORS_EMPTY = _envelope({"errors": []})


@pytest.fixture(autouse=True)
def _fresh_pageerror_tracker():
    """去重状态是模块级单例 —— 每例前后清空，保证用例互不污染。"""
    bt._pageerror_seen.clear()
    yield
    bt._pageerror_seen.clear()


class FakeCli:
    """browse_exec 替身：按 argv 区分主命令与捕获探针。

    捕获探针的实现契约是必带 ``--json``（读）或 ``--clear``（清）标记；
    不带标记的 console/errors 调用是 agent 自己的主命令。errors 流没有
    clear 调用（CLI 清不掉，见源码块注释）。``ops`` 记录 ("main", head) 与
    ("console"|"errors", "read"|"clear")。
    """

    def __init__(
        self,
        *,
        main_out: str = "ok",
        console_out: str = _CONSOLE_ONE_ERROR,
        errors_out: str = _ERRORS_ONE_UNCAUGHT,
        capture_exit: int = 0,
        console_clear_rc: int = 0,
        raise_on_capture: bool = False,
    ) -> None:
        self.main_out = main_out
        self.console_out = console_out
        self.errors_out = errors_out
        self.capture_exit = capture_exit
        self.console_clear_rc = console_clear_rc
        self.raise_on_capture = raise_on_capture
        self.ops: list[tuple[str, str]] = []

    async def __call__(
        self, argv, workspace, timeout_sec: int = 60, agent_id=None
    ):
        argv = [str(a) for a in argv]
        head = argv[0] if argv else ""
        if head in ("console", "errors") and (
            "--json" in argv or "--clear" in argv
        ):
            if self.raise_on_capture:
                raise FileNotFoundError("agent-browser binary not found")
            if "--clear" in argv:
                self.ops.append((head, "clear"))
                rc = self.console_clear_rc if head == "console" else 0
                return rc, "", ""
            self.ops.append((head, "read"))
            out = self.console_out if head == "console" else self.errors_out
            return self.capture_exit, out, ""
        self.ops.append(("main", head))
        return 0, self.main_out, ""


def _tiny_png(path: Path) -> None:
    # 1x1 PNG
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    path.write_bytes(raw)


@pytest.mark.asyncio
async def test_browse_reports_console_errors_and_clears_buffer(tmp_path, monkeypatch):
    """支持 console 的 CLI：错误原文进 [console] 段，attestation 拿真实计数；
    console 流读后立即清（窗口最小化），errors 流只读不去清（CLI 清不掉，
    由去重兜底），warning 不计入也不进文本。"""
    cli = FakeCli()
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    text = result.output or ""
    assert "[console] 2 browser console/page error(s):" in text
    assert "Uncaught ReferenceError: foliageMat is not defined" in text
    assert "Error: uncaught probe" in text
    assert "deprecated API used" not in text  # warning 不是错误，不进 [console]
    assert text.rstrip().endswith("[pageerror] Error: uncaught probe")
    assert attest_kwargs.get("console_errors") == 2
    # goto 主命令 → viewport 复位 → console 读 → console 清 → errors 读（无清）。
    assert cli.ops == [
        ("main", "goto"),
        ("main", "set"),
        ("console", "read"),
        ("console", "clear"),
        ("errors", "read"),
    ]


@pytest.mark.asyncio
async def test_browse_console_unsupported_fails_open_with_note(tmp_path, monkeypatch):
    """旧版 CLI（console/errors 子命令 exit≠0）：console_errors=0 但文本明确
    声明未捕获，browse 整体仍成功；读都失败时绝不清缓冲。"""
    cli = FakeCli(capture_exit=1)
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    text = result.output or ""
    assert CONSOLE_CAPTURE_UNAVAILABLE_NOTE in text
    assert text.rstrip().endswith(
        "如需排查前端错误请用 bash 跑浏览器开发者协议"
    )
    assert attest_kwargs.get("console_errors") == 0
    assert cli.ops == [
        ("main", "goto"),
        ("main", "set"),
        ("console", "read"),
        ("errors", "read"),
    ]  # 双侧读失败 → 不清缓冲（证据不销毁）


@pytest.mark.asyncio
async def test_browse_console_spawn_failure_fails_open(tmp_path, monkeypatch):
    """二进制缺失/daemon 挂死（spawn 抛异常）：同样 fail-open + 声明行。"""
    cli = FakeCli(raise_on_capture=True)
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["snapshot", "-i"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    assert CONSOLE_CAPTURE_UNAVAILABLE_NOTE in (result.output or "")
    assert attest_kwargs.get("console_errors") == 0


@pytest.mark.asyncio
async def test_browse_console_zero_errors_reports_clean_capture(tmp_path, monkeypatch):
    """读成功且零错误：明确给 `[console] 0 errors.`（区分「抓到了、干净」）。"""
    cli = FakeCli(console_out=_CONSOLE_EMPTY, errors_out=_ERRORS_EMPTY)
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    assert "[console] 0 errors." in (result.output or "")
    assert attest_kwargs.get("console_errors") == 0


@pytest.mark.asyncio
async def test_bad_payload_reports_unavailable_not_clean(tmp_path, monkeypatch):
    """坏 payload（rc=0 但非 JSON，如 >50KB 输出被截断）→ unavailable，
    绝不误报「0 errors」假干净。"""
    cli = FakeCli(console_out="not json at all", errors_out="")
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    text = result.output or ""
    assert CONSOLE_CAPTURE_UNAVAILABLE_NOTE in text
    assert "[console] 0 errors." not in text
    assert attest_kwargs.get("console_errors") == 0


@pytest.mark.asyncio
async def test_schema_drift_reports_unavailable_not_clean(tmp_path, monkeypatch):
    """键名漂移（data 是 dict 但期望键不是 list，双侧皆然）→ unavailable
    而非假阴性「0 errors」。"""
    cli = FakeCli(
        console_out=_envelope({"logs": []}),  # messages → logs（假想新键名）
        errors_out=_envelope({"failures": []}),  # errors → failures
    )
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    text = result.output or ""
    assert CONSOLE_CAPTURE_UNAVAILABLE_NOTE in text
    assert "[console] 0 errors." not in text
    assert attest_kwargs.get("console_errors") == 0


@pytest.mark.asyncio
async def test_close_and_quit_skip_capture_and_reset_tracker(tmp_path, monkeypatch):
    """close/quit：不触发捕获（ops 无探针），且本 agent 的 pageerror 去重
    状态随会话终止作废。"""
    cli = FakeCli()
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")

    async def fake_attest(**kwargs):
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    bt._pageerror_seen["qa-1"] = {"[pageerror] old": None}
    result = await browse_tool(BrowseParams(args=["close"]), "qa-1", str(tmp_path))
    assert result.success is True
    assert cli.ops == [("main", "close")]
    assert "qa-1" not in bt._pageerror_seen

    result = await browse_tool(BrowseParams(args=["quit"]), "qa-1", str(tmp_path))
    assert result.success is True
    assert cli.ops == [("main", "close"), ("main", "quit")]
    assert "qa-1" not in bt._pageerror_seen


@pytest.mark.asyncio
async def test_console_clear_failure_declares_possible_repeat(tmp_path, monkeypatch):
    """console 清理 rc≠0（如超时 rc=-1，browse_exec 不抛）：本批照常计数，
    但 note 声明下条命令可能重复本批。"""
    cli = FakeCli(console_clear_rc=-1)
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    attest_kwargs: dict = {}

    async def fake_attest(**kwargs):
        attest_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    text = result.output or ""
    assert CONSOLE_CLEAR_FAILED_NOTE in text
    assert "可能重复本批" in text
    assert attest_kwargs.get("console_errors") == 2  # 本批照常真实计数


@pytest.mark.asyncio
async def test_browse_console_command_skips_capture(tmp_path, monkeypatch):
    """agent 主动读 console 时，捕获不得吃掉缓冲（无 read/clear 探针）。"""
    cli = FakeCli(main_out="[error] something")
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")

    async def fake_attest(**kwargs):
        return ""

    monkeypatch.setattr(bt, "issue_browse_e2e_attestation", fake_attest)

    result = await browse_tool(
        BrowseParams(args=["console"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    # 仅主命令本身一次 spawn（read 语义来自 agent 自己的 console 命令）；
    # 捕获既不追加探针，也绝不 clear。
    assert cli.ops == [("main", "console")]
    assert "[error] something" in (result.output or "")


@pytest.mark.asyncio
async def test_browse_e2e_attestation_receives_real_console_count(tmp_path, monkeypatch):
    """真实 issue_browse_e2e_attestation 路径：attestation_service.create 收到
    真实 console_errors（替换原硬编码 0）。"""
    cli = FakeCli()
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    monkeypatch.setattr(
        "hiveweave.tools.helpers.get_project_id", AsyncMock(return_value="p1")
    )
    monkeypatch.setattr(
        "hiveweave.tools.bash._resolve_test_attestation_task_id",
        AsyncMock(return_value=("v-1", "")),
    )
    monkeypatch.setattr(
        "hiveweave.services.task.TaskService.get_task",
        AsyncMock(
            return_value={
                "id": "v-1",
                "title": "UI polish",
                "status": "running",
                "policy_id": "",
            }
        ),
    )
    monkeypatch.setattr(
        "hiveweave.services.task.TaskService._is_verify_task",
        lambda *a, **k: False,
    )
    created: dict = {}

    async def fake_create(*args, **kwargs):
        created.update(kwargs)
        return "att-1"

    monkeypatch.setattr(
        "hiveweave.services.attestation.attestation_service.create", fake_create
    )
    monkeypatch.setattr(
        "hiveweave.services.attestation.hash_stdout", lambda blob: "h"
    )
    monkeypatch.setattr(bt, "_maybe_git_commit", AsyncMock(return_value="abc"))

    result = await browse_tool(
        BrowseParams(args=["goto", "http://127.0.0.1:3000"]),
        "qa-1",
        str(tmp_path),
    )

    assert result.success is True
    assert "attestation_id=att-1" in (result.output or "")
    assert created.get("console_errors") == 2
    assert created.get("kind") == "browse_e2e"


@pytest.mark.asyncio
async def test_assert_visual_records_console_errors(tmp_path, monkeypatch):
    """visual_check 路径（原硬编码 0 的第二处）：同样捕获并计数。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    png = ws / "evidence" / "flow.png"
    png.parent.mkdir()
    _tiny_png(png)

    cli = FakeCli(console_out=_CONSOLE_ONE_ERROR, errors_out=_ERRORS_EMPTY)
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    monkeypatch.setattr(
        "hiveweave.tools.helpers.get_project_id", AsyncMock(return_value="p1")
    )
    monkeypatch.setattr(
        "hiveweave.tools.bash._resolve_test_attestation_task_id",
        AsyncMock(return_value=(None, "")),
    )
    created: dict = {}

    async def fake_create(*args, **kwargs):
        created.update(kwargs)
        return "att-9"

    monkeypatch.setattr(
        "hiveweave.services.attestation.attestation_service.create", fake_create
    )
    monkeypatch.setattr(
        "hiveweave.services.attestation.hash_stdout", lambda blob: "h"
    )
    monkeypatch.setattr(bt, "_maybe_git_commit", AsyncMock(return_value="abc"))

    result = await assert_visual_tool(
        AssertVisualParams(
            screenshot_path="evidence/flow.png",
            observed=(
                "Level select shows 3 cards; Start button bottom-right; "
                "no error overlay; HUD timer top-left reads 00:30."
            ),
            verdict="pass",
        ),
        "qa-1",
        str(ws),
    )

    assert result.success is True
    text = result.output or ""
    assert "[console] 1 browser console/page error(s):" in text
    assert "foliageMat is not defined" in text
    assert created.get("console_errors") == 1
    assert created.get("kind") == "visual_check"


@pytest.mark.asyncio
async def test_assert_visual_keeps_console_text_when_stamp_fails(tmp_path, monkeypatch):
    """N-5：捕获（可能已清 console 缓冲）之后 create 抛异常 → 错误文本必须
    携带已抓到的 [console] 内容，不能随异常一起蒸发。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    png = ws / "evidence" / "flow.png"
    png.parent.mkdir()
    _tiny_png(png)

    cli = FakeCli(console_out=_CONSOLE_ONE_ERROR, errors_out=_ERRORS_EMPTY)
    monkeypatch.setattr(bt, "browse_exec", cli)
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    monkeypatch.setattr(
        "hiveweave.tools.helpers.get_project_id", AsyncMock(return_value="p1")
    )
    monkeypatch.setattr(
        "hiveweave.tools.bash._resolve_test_attestation_task_id",
        AsyncMock(return_value=(None, "")),
    )

    async def failing_create(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "hiveweave.services.attestation.attestation_service.create", failing_create
    )
    monkeypatch.setattr(
        "hiveweave.services.attestation.hash_stdout", lambda blob: "h"
    )
    monkeypatch.setattr(bt, "_maybe_git_commit", AsyncMock(return_value="abc"))

    result = await assert_visual_tool(
        AssertVisualParams(
            screenshot_path="evidence/flow.png",
            observed=(
                "Level select shows 3 cards; Start button bottom-right; "
                "no error overlay; HUD timer top-left reads 00:30."
            ),
            verdict="pass",
        ),
        "qa-1",
        str(ws),
    )

    assert result.success is False
    text = result.error or ""
    assert "assert_visual attestation failed" in text
    # 缓冲已清 → 这段文本是错误的唯一载体，必须随异常返回
    assert "foliageMat is not defined" in text


@pytest.mark.asyncio
async def test_pageerror_dedup_across_reads_and_reset(tmp_path, monkeypatch):
    """errors 缓冲清不掉 → 同文本 pageerror 只在上报首次计数；reset（会话
    重建）后同文本重新上报。console 流不受去重影响（读后即清）。"""
    monkeypatch.setattr(bt, "resolve_browse_bin", lambda: "fake-ab")
    reads = {"n": 0}

    async def fake_exec(argv, workspace, timeout_sec=60, agent_id=None):
        argv = [str(a) for a in argv]
        if argv[:1] == ["console"] and "--json" in argv:
            reads["n"] += 1
            return (
                0,
                _envelope({"messages": [{"type": "error", "text": f"c{reads['n']}"}]}),
                "",
            )
        if argv[:1] == ["errors"] and "--json" in argv:
            return 0, _envelope({"errors": [{"text": "Error: same-uncaught"}]}), ""
        return 0, "", ""

    monkeypatch.setattr(bt, "browse_exec", fake_exec)

    count1, note1 = await bt.capture_browser_console(str(tmp_path), "qa-dedup")
    count2, note2 = await bt.capture_browser_console(str(tmp_path), "qa-dedup")
    assert (count1, count2) == (2, 1)
    assert "[pageerror] Error: same-uncaught" in note1
    assert "same-uncaught" not in note2

    bt._reset_pageerror_tracker("qa-dedup")
    count3, note3 = await bt.capture_browser_console(str(tmp_path), "qa-dedup")
    assert count3 == 2
    assert "same-uncaught" in note3


def test_console_entries_ignore_non_error_levels():
    console_entries, pageerror_entries = bt._console_error_entries(
        {
            "messages": [
                {"type": "error", "text": "boom"},
                {"type": "warning", "text": "meh"},
                {"type": "log", "text": "hi"},
            ]
        },
        {"errors": [{"text": "Error: uncaught\n    at x:1"}]},
    )
    assert console_entries == ["[error] boom"]
    assert pageerror_entries == ["[pageerror] Error: uncaught\n    at x:1"]
    # 非 dict / 空数据不抛
    assert bt._console_error_entries(None, None) == ([], [])


def test_pageerror_dedup_cap_rebuilds_ledger():
    bt._pageerror_seen.clear()
    entries = [f"[pageerror] e{i}" for i in range(600)]
    fresh = bt._dedup_pageerrors("cap-agent", entries)
    assert fresh == entries
    ledger = bt._pageerror_seen["cap-agent"]
    assert len(ledger) <= bt._ERROR_SEEN_CAP
    # cap 重建后，被遗忘的旧文本（不在最近 500 内）会重报 —— 宁重报不漏报。
    fresh2 = bt._dedup_pageerrors("cap-agent", entries[:10])
    assert fresh2 == entries[:10]


def test_render_console_note_caps_lines_and_bytes():
    entries = [f"[error] e{i}" for i in range(200)]
    note = bt._render_console_note(200, entries)
    lines = note.splitlines()
    assert lines[0] == "[console] 200 browser console/page error(s):"
    body = [l for l in lines[1:] if l != "[console] …(truncated)"]
    assert len(body) <= bt._CONSOLE_TEXT_MAX_LINES
    assert note.endswith("[console] …(truncated)")
    # 4KB 上限（含 header/truncated 标记的余量）
    assert len(note.encode("utf-8")) <= bt._CONSOLE_TEXT_MAX_BYTES + 200

    small = bt._render_console_note(1, ["[error] only one"])
    assert small == (
        "[console] 1 browser console/page error(s):\n[console] [error] only one"
    )
