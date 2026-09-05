"""VERIFY 验收清单（acceptance_criteria）覆盖门。

门禁智能化包任务6：VERIFY 任务提交时，若任务记录带非空
``acceptance_criteria``，verdict evidence 必须逐条体现覆盖 —— 条目原文
（或其编号 ``条目N`` / ``第N条`` / ``#N`` / ``item N``）在 evidence 文本中
可匹配，或对不适用条目显式标注「N/A: <理由>」。缺覆盖 → 由调用方（E1 门
/ submit 聚合预检）拒绝并列出缺哪几条 + 处方。

纯机械匹配（大小写/空白归一后的原文子串、编号引用、N/A 绑定），不做语义
判断；本模块只负责**判定与点名**，拒绝动作在调用方。``check_evidence_verifiable``
对 VERIFY 的既有跳过逻辑不受影响（那是 approve 侧路径证据校验，语义不同）。
"""
from __future__ import annotations

import json
import re
from typing import Any

# 显式 N/A：N/A 或 NA，后接 ：:-— 分隔的非空理由（≥2 字符）
_NA_RE = re.compile(r"N/?A\s*[:：—-]\s*\S{2,}", re.IGNORECASE)

_WS_RE = re.compile(r"\s+")


def _norm(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip().lower()


def parse_acceptance_items(criteria: Any) -> list[str]:
    """acceptance_criteria → 非空条目文本 list。

    接受 list[str] / list[dict{text,...}] / JSON 字符串 / 单条字符串。
    dict 形态（verify_spawn 系统模板）取 ``text``（退化 description /
    criterion / 整体字符串化）。
    """
    if criteria is None:
        return []
    items: Any
    if isinstance(criteria, str):
        try:
            parsed = json.loads(criteria)
        except Exception:
            items = [criteria]
        else:
            items = parsed if isinstance(parsed, list) else [parsed]
    elif isinstance(criteria, (list, tuple)):
        items = list(criteria)
    else:
        items = [criteria]
    out: list[str] = []
    for raw in items:
        if isinstance(raw, dict):
            text = (
                raw.get("text")
                or raw.get("description")
                or raw.get("criterion")
                or ""
            )
            text = str(text or "").strip() or str(raw).strip()
        else:
            text = str(raw or "").strip()
        if text:
            out.append(text)
    return out


def _index_ref_patterns(index: int) -> list[re.Pattern[str]]:
    """条目编号引用形态（对 CJK 邻接字符放宽，只要求后随位不是数字）。

    ``条目3`` / ``第 3 条`` / ``#3`` / ``item 3`` / ``criteria 3`` / ``验收 3``。
    """
    i = index
    return [
        re.compile(rf"条目\s*{i}(?!\d)"),
        re.compile(rf"第\s*{i}\s*条"),
        re.compile(rf"验收项?\s*{i}(?!\d)"),
        re.compile(rf"#{i}(?!\d)"),
        re.compile(rf"\bitem\s*{i}(?!\d)", re.IGNORECASE),
        re.compile(rf"\bcriteria\s*{i}(?!\d)", re.IGNORECASE),
    ]


def _evidence_text(evidence: dict[str, Any] | None) -> str:
    """拼接 evidence 中会出现逐条覆盖陈述的文本字段。"""
    if not isinstance(evidence, dict):
        return ""
    parts: list[str] = []
    for key in (
        "summary",
        "verdict_summary",
        "conclusion",
        "test_output",
        "env_snapshot",
        "review_notes",
    ):
        val = evidence.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    for key in ("blocking_issues", "coverage", "checklist"):
        val = evidence.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if x)
        elif isinstance(val, dict):
            parts.extend(f"{k}: {v}" for k, v in val.items() if v)
    return "\n".join(parts)


def uncovered_acceptance_items(
    criteria: Any, evidence: dict[str, Any] | None
) -> list[str]:
    """返回未被 evidence 覆盖的条目标签（``条目N: <原文>``）；空 = 全覆盖。

    匹配顺序：① 编号引用（含同行 N/A）→ ② 条目原文归一子串 →
    ③ 无编号裸 N/A 池（按条目顺序一对一豁免）。
    """
    items = parse_acceptance_items(criteria)
    if not items:
        return []
    raw_text = _evidence_text(evidence)
    if not raw_text.strip():
        return [f"条目{i}: {t}" for i, t in enumerate(items, 1)]
    text = _norm(raw_text)

    na_count = len(_NA_RE.findall(raw_text))

    def _line_indexes(ln: str) -> set[int]:
        found: set[int] = set()
        for i in range(1, len(items) + 1):
            if any(p.search(ln) for p in _index_ref_patterns(i)):
                found.add(i)
        return found

    covered: set[int] = set()
    na_used = 0
    # ① 编号引用；同「行」内 N/A 直接绑定该条目（"条目2 N/A: 环境不可得"）
    for ln in raw_text.splitlines():
        ln_norm = _norm(ln)
        idxs = _line_indexes(ln_norm)
        has_na = bool(_NA_RE.search(ln))
        for i in idxs:
            covered.add(i)
            if has_na:
                na_used += 1
    # ② 条目原文（归一后全长子串）
    for i, raw in enumerate(items, 1):
        if i in covered:
            continue
        if _norm(raw) and _norm(raw) in text:
            covered.add(i)
    # ③ 裸 N/A 池：未被编号绑定的 N/A 按条目顺序一对一豁免
    bare_na = max(0, na_count - na_used)
    if bare_na:
        for i, _raw in enumerate(items, 1):
            if bare_na <= 0:
                break
            if i not in covered:
                covered.add(i)
                bare_na -= 1

    return [
        f"条目{i}: {raw}" for i, raw in enumerate(items, 1) if i not in covered
    ]


def format_acceptance_coverage_error(missing: list[str]) -> str:
    """E1 验收清单缺覆盖的拒绝文案（点名缺哪几条 + 处方）。"""
    return (
        "SUBMIT REJECTED (verify acceptance checklist): 任务带 "
        "acceptance_criteria，verdict evidence 未体现对以下 "
        f"{len(missing)} 条的覆盖：\n- "
        + "\n- ".join(missing)
        + "\n处方：在 verdict/blockingIssues/summary 中逐条引用条目原文或"
        "编号（条目N / 第N条 / #N / item N）说明验证结果；不适用的条目"
        "显式标注「N/A: <理由>」（理由非空）。acceptance_criteria 为空的"
        "任务不受此门影响。"
    )
