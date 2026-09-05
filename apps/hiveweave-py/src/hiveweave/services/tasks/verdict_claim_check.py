"""VERIFY FAIL blockingIssues 文件级主张机械复核（verdict_claim_check）。

门禁智能化包任务4（保守首版）：VERIFY 任务提交 verdict=FAIL 且带
blocking_issues 时，对其中「``<path> 无/缺/没有/missing <symbol>``」类
**文件级主张**做机械复核 —— 解析主张（中英模式，解析不了的跳过），在对应
文件内容（evidence commit → implementer/assignee worktree → MAIN，取
可靠且现成的来源）里对 symbol 做 substring grep。

红线：结果只作为**事实位**返回，由调用方附在提交回执与 verdict 证据
（evidence.claim_check）里让 reviewer 看到；本模块**绝不**自动拒绝提交
或翻转 verdict。
"""
from __future__ import annotations

import re
from typing import Any

# 主张容量上限（防超长 blockingIssues 拖垮提交路径）
MAX_ISSUES_SCANNED = 8
MAX_CLAIMS_PER_ISSUE = 3
MAX_CLAIMS_TOTAL = 10
_MAX_FILE_BYTES = 1_000_000

# 文件路径形态（必须带扩展名，避免把散文词当路径）
_PATH = r"(?P<path>[A-Za-z0-9_./\\\-]+\.[A-Za-z0-9]{1,5})"
# 标识符形态（允许 a.b 与 $）
_SYM = r"[`'\"]?(?P<sym>[A-Za-z_$][\w.$]*)[`'\"]?"

_CLAIM_PATTERNS: list[re.Pattern[str]] = [
    # EN: "<path> is missing <sym>" / "<path> missing <sym>"
    re.compile(
        rf"{_PATH}\s+(?:is\s+)?missing\s+(?:the\s+)?{_SYM}", re.IGNORECASE
    ),
    # EN: "<sym> is missing in/from/on <path>"
    re.compile(
        rf"{_SYM}\s+(?:is\s+)?missing\s+(?:in|from|on)\s+[`'\"]?{_PATH}",
        re.IGNORECASE,
    ),
    # EN: "<path> does not contain/have/define/export/implement <sym>"
    re.compile(
        rf"{_PATH}\s+(?:does\s+not|doesn'?t)\s+"
        rf"(?:contain|have|define|export|implement|include|provide)\s+{_SYM}",
        re.IGNORECASE,
    ),
    # EN: "<sym> not found/defined/present in <path>"
    re.compile(
        rf"{_SYM}\s+(?:is\s+)?not\s+(?:found|defined|implemented|present)\s+"
        rf"(?:in|on)\s+[`'\"]?{_PATH}",
        re.IGNORECASE,
    ),
    # ZH: "<path>(中) 缺少/缺/没有/无/未定义/未实现/未导出/未包含/未找到/找不到/不含 <sym>"
    re.compile(
        rf"{_PATH}\s*(?:中|里|内)?\s*"
        rf"(?:缺少|缺|没有|无|未定义|未实现|未导出|未包含|未找到|找不到|不含)\s*{_SYM}"
    ),
    # ZH: "<sym> 在 <path>(中) 缺失/不存在/未定义/未实现/找不到"
    re.compile(
        rf"{_SYM}\s*(?:在|于)\s*[`'\"]?{_PATH}[`'\"]?\s*(?:中|里|内)?\s*"
        rf"(?:缺失|不存在|未定义|未实现|找不到)"
    ),
]


def parse_file_claims(text: str) -> list[dict[str, str]]:
    """解析一条 blockingIssue 里的文件级主张；解析不出 → 空 list（跳过）。"""
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pat in _CLAIM_PATTERNS:
        for m in pat.finditer(text or ""):
            path = (m.group("path") or "").replace("\\", "/").strip()
            sym = (m.group("sym") or "").strip()
            if not path or not sym:
                continue
            key = (path.lower(), sym)
            if key in seen:
                continue
            seen.add(key)
            claims.append({"claim": (text or "").strip(), "path": path, "symbol": sym})
            if len(claims) >= MAX_CLAIMS_PER_ISSUE:
                return claims
    return claims


async def _read_claim_target(
    project_id: str, task: dict[str, Any], evidence: dict[str, Any], rel: str
) -> tuple[str | None, str]:
    """按可靠且现成的优先级取主张目标文件内容，返回 (内容, 来源说明)。"""
    from pathlib import Path

    from hiveweave.services.worktree_review import (
        agent_worktree_path,
        normalize_evidence_path,
        project_main_workspace,
    )

    rel_n = normalize_evidence_path(rel)
    if not rel_n:
        return None, ""

    # ① evidence.commit / commit_hash：git show 该修订（VERIFY 的代码在 MAIN）
    commit = str(
        evidence.get("commit")
        or evidence.get("commit_hash")
        or task.get("merge_commit")
        or ""
    ).strip()
    # P2（审计）：commit 以 f"{commit}:{rel}" 拼参传给 ``git show``，以
    # ``-`` 开头的值可被 git 当作选项解析（选项位注入）。命中 → 不执行
    # git show 且该主张直接 skipped（来源不可信，不回落 worktree/MAIN）。
    if commit.startswith("-"):
        return None, (
            f"commit 以 '-' 开头（疑似选项注入），已拒绝执行 git show："
            f"{commit[:24]}"
        )
    if commit:
        main_ws = await project_main_workspace(project_id)
        if main_ws:
            try:
                from hiveweave.services.git_worktree import _git

                ok, out = await _git(
                    ["show", f"{commit}:{rel_n.replace(chr(92), '/')}"], main_ws
                )
                if ok and out:
                    return out, f"commit {commit[:12]}:{rel_n}"
            except Exception:
                pass

    # ② implementer / assignee worktree
    for agent in (task.get("implementer_id"), task.get("assignee_id")):
        if not agent:
            continue
        wt = (task.get("implementer_worktree") or "").strip()
        if not wt or not Path(wt).is_dir():
            wt = await agent_worktree_path(str(agent)) or ""
        if wt:
            f = Path(wt) / rel_n
            try:
                if f.is_file() and f.stat().st_size <= _MAX_FILE_BYTES:
                    return (
                        f.read_text(encoding="utf-8", errors="replace"),
                        f"worktree {rel_n}",
                    )
            except OSError:
                continue

    # ③ MAIN workspace
    main_ws = await project_main_workspace(project_id)
    if main_ws:
        f = Path(main_ws) / rel_n
        try:
            if f.is_file() and f.stat().st_size <= _MAX_FILE_BYTES:
                return (
                    f.read_text(encoding="utf-8", errors="replace"),
                    f"MAIN {rel_n}",
                )
        except OSError:
            pass
    return None, ""


async def run_verdict_claim_check(
    project_id: str,
    task: dict[str, Any],
    evidence: dict[str, Any],
) -> list[dict[str, str]]:
    """对 verdict=FAIL 的 blockingIssues 文件级主张做机械复核。

    返回事实位列表（每项 {claim, path, symbol, result, detail}），
    result ∈ refuted（文件中存在 symbol）/ verified（未找到）/ skipped
    （无法解析或无法读取）。调用方决定是否附到回执/证据 —— 本函数无副作用。
    """
    blocking = evidence.get("blocking_issues")
    if not isinstance(blocking, list):
        return []
    results: list[dict[str, str]] = []
    for issue in blocking[:MAX_ISSUES_SCANNED]:
        text = str(issue or "").strip()
        if not text:
            continue
        claims = parse_file_claims(text)
        if not claims:
            continue
        for c in claims:
            if len(results) >= MAX_CLAIMS_TOTAL:
                return results
            content, source = await _read_claim_target(
                project_id, task, evidence, c["path"]
            )
            if content is None:
                results.append(
                    {
                        **c,
                        "result": "skipped",
                        "detail": source
                        or f"无法读取 {c['path']}（commit/worktree/MAIN 均未找到）",
                    }
                )
                continue
            if c["symbol"] in content:
                results.append(
                    {
                        **c,
                        "result": "refuted",
                        "detail": f"{c['path']} 中存在 {c['symbol']}（来源: {source}）",
                    }
                )
            else:
                results.append(
                    {
                        **c,
                        "result": "verified",
                        "detail": f"未在 {c['path']} 中找到 {c['symbol']}（来源: {source}）",
                    }
                )
    return results


def format_claim_check_lines(results: list[dict[str, str]]) -> list[str]:
    """事实位回执行：`claim_check: "<主张>" → refuted/verified/skipped（…）`。"""
    lines: list[str] = []
    for r in results:
        res = r.get("result") or "skipped"
        lines.append(
            f'claim_check: "{r.get("claim")}" → {res}'
            f"（{r.get('detail') or '无法解析'}）"
        )
    return lines
