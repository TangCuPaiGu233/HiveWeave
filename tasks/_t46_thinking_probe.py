# -*- coding: utf-8 -*-
"""只读取证：TEST_DSH_46 的 chat_messages 思考段 + agent 模型配置。"""
import json
import sqlite3
import sys

DB = r"D:\PC_AI\Project\HiveTestProject\TEST_DSH_46\.hiveweave\data.db"
META = r"D:\PC_AI\Project\HiveWeave\apps\hiveweave-py\data\hiveweave.db"


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    print("=== chat_messages 概览 ===")
    rows = conn.execute(
        "SELECT id, agent_id, role, is_streaming, created_at, metadata, "
        "length(content) AS clen, length(coalesce(metadata,'')) AS mlen "
        "FROM chat_messages ORDER BY created_at DESC LIMIT 12"
    ).fetchall()
    seg_stat = {}
    thinking_samples = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata"] or "{}")
        except Exception:
            pass
        segs = meta.get("segments") or []
        types = [s.get("type") for s in segs if isinstance(s, dict)]
        for t in types:
            seg_stat[t] = seg_stat.get(t, 0) + 1
        for s in segs:
            if isinstance(s, dict) and s.get("type") == "thinking":
                thinking_samples.append(
                    (r["id"][:8], str(s.get("content", ""))[:120])
                )
        print(f"{r['id'][:8]} role={r['role']:9s} segs={types[:6]} "
              f"clen={r['clen']} mlen={r['mlen']}")
    print("\n=== 段类型统计（最近12条） ===")
    print(seg_stat)
    print(f"\n=== thinking 样本 {len(thinking_samples)} ===")
    for sid, txt in thinking_samples[:5]:
        print(f"[{sid}] {txt}")

    print("\n=== agents 模型配置 ===")
    cols = [c[1] for c in conn.execute("PRAGMA table_info(agents)")]
    mcols = [c for c in cols if "model" in c.lower() or "thinking" in c.lower()]
    print("model-ish cols:", mcols)
    if mcols:
        sel = ", ".join(["id", "name", "role"] + mcols)
        for a in conn.execute(f"SELECT {sel} FROM agents LIMIT 15"):
            print(dict(a))
    conn.close()

    print("\n=== meta llm_models ===")
    m = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    m.row_factory = sqlite3.Row
    mcols2 = [c[1] for c in m.execute("PRAGMA table_info(llm_models)")]
    print("cols:", mcols2)
    for r in m.execute("SELECT * FROM llm_models"):
        d = dict(r)
        print({k: d.get(k) for k in d if k in (
            "id", "name", "provider", "model_id", "base_url", "enabled",
            "thinking", "thinking_intensity", "reasoning", "tier",
            "management_tier", "executor_tier", "supports_thinking")})
    m.close()


if __name__ == "__main__":
    sys.exit(main())
