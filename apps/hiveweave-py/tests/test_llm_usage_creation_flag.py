"""42 轮 P2-9：cache_creation 落账打标（creation_unreported）+ 样本不足判据。

背景：双项目 cache_creation 全 0 = 分母缺分量，命中率 94.77/95.27 均为
乐观上限。三件事：
1. 上游未回传 cache 写入（usage 缺字段 / provider 族不上报）→ 该行
   cache_creation_tokens 显式记 0 且 creation_unreported=1；上游真回传
   （含真 0）→ creation_unreported=0，两者可区分。
2. usage 行数 < MIN_CACHE_HIT_SAMPLE_ROWS(20) 的聚合输出「样本不足」
   而非命中率数值。
3. 迁移：新库 CREATE 原生带列；旧库 ALTER 补列（同 cold_start 模式），
   PROJECT_DB_COLUMN_CHECKS fail-loud 防迁移断裂。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hiveweave.db import project as project_db
from hiveweave.services.token_meter import (
    INSUFFICIENT_SAMPLE_NOTE,
    MIN_CACHE_HIT_SAMPLE_ROWS,
    token_meter,
)

PROJECT_ID = "creation-flag"
AGENT = "agent-flag"


@pytest.fixture
async def env():
    """临时 workspace + Meta 路由 patch（agent→project→workspace）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = str(Path(tmpdir).resolve())

        async def fake_pid(agent_id: str):
            return PROJECT_ID if agent_id == AGENT else None

        async def fake_ws(pid: str):
            return ws if pid == PROJECT_ID else None

        with patch(
            "hiveweave.db.meta.get_agent_project_id", side_effect=fake_pid
        ), patch(
            "hiveweave.db.meta.get_project_workspace", side_effect=fake_ws
        ):
            yield {"ws": ws}

        async with project_db._ensure_lock:
            conn = project_db._cache.pop(ws, None)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        project_db._agent_cache.pop(AGENT, None)
        project_db._write_locks.pop(ws, None)


async def _rows(conn, where: str) -> list:
    cur = await conn.execute(
        "SELECT cache_creation_tokens, creation_unreported, cold_start "
        f"FROM llm_usage WHERE {where} ORDER BY rowid"
    )
    return [tuple(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_unreported_creation_marked_zero(env):
    """未回传（OpenAI 系无 cache_creation_reported 位）→ 记 0 + 打标 1。"""
    conn = await project_db.ensure_project_db(env["ws"])
    await token_meter.record_rounds(
        agent_id=AGENT, project_id=PROJECT_ID, run_id="run-unreported",
        rounds=[
            # normalize_usage(openai 系) 产出的轮：无 cache_creation_reported
            {"input": 100, "output": 20, "cache_read": 80,
             "cache_creation": 0, "total": 120},
            # 显式 reported=0 同样视为未回传
            {"input": 50, "output": 10, "cache_read": 0,
             "cache_creation": 0, "total": 60, "cache_creation_reported": 0},
        ],
        model_id="m", provider="openai-responses", request_type="main",
    )
    rows = await _rows(conn, "run_id='run-unreported'")
    assert len(rows) == 2
    for cache_creation, unreported, _cold in rows:
        assert cache_creation == 0
        assert unreported == 1  # 0 是「无数据」，不是「确实为 0」


@pytest.mark.asyncio
async def test_reported_creation_stores_true_value_unmarked(env):
    """上游真回传（含真 0）→ 落真值且不打标；与「没回传」可区分。"""
    conn = await project_db.ensure_project_db(env["ws"])
    await token_meter.record_rounds(
        agent_id=AGENT, project_id=PROJECT_ID, run_id="run-reported",
        rounds=[
            # Anthropic 真回传 cache 写入 123
            {"input": 10, "output": 5, "cache_read": 200,
             "cache_creation": 123, "total": 138,
             "cache_creation_reported": 1},
            # Anthropic 真回传 0 —— 必须与「未回传」可区分
            {"input": 10, "output": 5, "cache_read": 300,
             "cache_creation": 0, "total": 15,
             "cache_creation_reported": 1},
        ],
        model_id="m", provider="anthropic", request_type="main",
    )
    rows = await _rows(conn, "run_id='run-reported'")
    assert rows[0] == (123, 0, 0)
    assert rows[1] == (0, 0, 0)


@pytest.mark.asyncio
async def test_compaction_row_carries_unreported_flag(env):
    """压缩路径同口径：未回传打标 1，回传不打标。"""
    conn = await project_db.ensure_project_db(env["ws"])
    await token_meter.record_compaction(
        agent_id=AGENT, model_id="m", input_tokens=10, output_tokens=5,
        cache_read_tokens=0, cache_creation_tokens=0,
        kind="conversation", provider="openai-responses",
        creation_unreported=1,
    )
    await token_meter.record_compaction(
        agent_id=AGENT, model_id="m", input_tokens=10, output_tokens=5,
        cache_read_tokens=0, cache_creation_tokens=7,
        kind="conversation", provider="anthropic",
        creation_unreported=0,
    )
    rows = await _rows(conn, "request_type='compaction_conversation'")
    assert rows[0][:2] == (0, 1)
    assert rows[1][:2] == (7, 0)


@pytest.mark.asyncio
async def test_cache_hit_insufficient_sample_note(env):
    """判据：usage 行数 <20 → 命中率输出「样本不足」；≥20 → 恢复数值。"""
    await project_db.ensure_project_db(env["ws"])

    async def add(n: int) -> None:
        await token_meter.record_rounds(
            agent_id=AGENT, project_id=PROJECT_ID, run_id=f"run-sample-{n}",
            rounds=[{"input": 10, "output": 5, "cache_read": 60,
                     "cache_creation": 0, "total": 15,
                     "cache_creation_reported": 1}
                    for _ in range(n)],
            model_id="m", provider="anthropic", request_type="main",
        )

    await add(MIN_CACHE_HIT_SAMPLE_ROWS - 1)  # 19 行
    summary = await token_meter.agent_summary(PROJECT_ID, AGENT)
    assert summary["cache_hit_percent"] == INSUFFICIENT_SAMPLE_NOTE

    await add(1)  # 20 行
    summary = await token_meter.agent_summary(PROJECT_ID, AGENT)
    assert isinstance(summary["cache_hit_percent"], int)
    # 分母 = input+cache_read+cache_creation = 20*(10+60+0) = 1400；分子 1200
    assert summary["cache_hit_percent"] == 86

    by_agent = await token_meter.project_by_agent(PROJECT_ID)
    assert by_agent[0]["cache_hit_percent"] == 86


@pytest.mark.asyncio
async def test_legacy_db_without_column_self_heals(env):
    """旧库缺 creation_unreported → ALTER 补列自愈，聚合照常（不炸）。"""
    import aiosqlite
    import os

    db_dir = Path(env["ws"]) / ".hiveweave"
    os.makedirs(db_dir, exist_ok=True)
    async with aiosqlite.connect(str(db_dir / "data.db")) as pre:
        await pre.execute(
            """
            CREATE TABLE llm_usage (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                project_id TEXT,
                run_id TEXT,
                task_id TEXT,
                model_id TEXT,
                request_type TEXT DEFAULT 'main',
                provider TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        await pre.commit()

    conn = await project_db.ensure_project_db(env["ws"])
    cur = await conn.execute("PRAGMA table_info(llm_usage)")
    cols = {row[1] for row in await cur.fetchall()}
    assert {"cold_start", "creation_unreported"} <= cols

    # 自愈后落账 + 聚合全链路不炸
    await token_meter.record_rounds(
        agent_id=AGENT, project_id=PROJECT_ID, run_id="run-heal",
        rounds=[{"input": 7, "output": 3, "total": 10}],
        request_type="main",
    )
    summary = await token_meter.agent_summary(PROJECT_ID, AGENT)
    assert summary["llm_calls"] == 1
