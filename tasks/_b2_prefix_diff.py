# -*- coding: utf-8 -*-
"""批次2c：45 轮快照相邻前缀 diff——「动态前缀抖动」确证（只读）。

对 tasks/_v45_snap 的 s3c10.db / dsh45.db 的 conversation_turns.raw_messages
做同 agent 相邻 turn 前缀对比：定位首个分歧 index、分歧发生段
（system1/compacted/history/system2/user）、分歧消息的 role 与差异样例。
"""
import json
import sqlite3
import sys

DBS = {
    "s3c10": r"D:\PC_AI\Project\HiveWeave\tasks\_v45_snap\s3c10.db",
    "dsh45": r"D:\PC_AI\Project\HiveWeave\tasks\_v45_snap\dsh45.db",
}


def load_turns(conn):
    rows = conn.execute(
        "select agent_id, turn_index, raw_messages from conversation_turns "
        "order by agent_id, turn_index"
    ).fetchall()
    out = {}
    for agent_id, seq, raw in rows:
        try:
            msgs = json.loads(raw)
        except Exception:
            continue
        out.setdefault(agent_id, []).append((seq, msgs))
    # conversation_turns 每行是该轮的**增量**（append_turn 落库 filtered_new），
    # 先按轮累计重建历史，再对比相邻前缀（增量对比无意义）。
    acc = {}
    for agent_id, seqs in out.items():
        hist: list[dict] = []
        acc_turns = []
        for seq, msgs in seqs:
            hist = hist + list(msgs)
            acc_turns.append((seq, list(hist)))
        acc[agent_id] = acc_turns
    return acc


def classify(pos, msgs):
    """按消息下标粗判段落（system1 / system2 / history / user 尾部）。"""
    if pos >= len(msgs):
        return "tail(new message)"
    role = msgs[pos].get("role")
    if pos == 0:
        return "system1(identity)"
    if role == "system":
        return "system(context)"
    return f"{role}@{pos}"


def first_diverge(prev, cur):
    n = min(len(prev), len(cur))
    for i in range(n):
        if prev[i] != cur[i]:
            return i
    return n if len(prev) != len(cur) else None


def brief(msg, limit=160):
    s = json.dumps(msg, ensure_ascii=False)
    return s[:limit]


def main():
    for tag, path in DBS.items():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            turns = load_turns(conn)
        finally:
            conn.close()
        print("=" * 78)
        print(f"[{tag}] agents={len(turns)}")
        total_pairs = append_pairs = rewrite_pairs = 0
        rewrites = []
        for agent_id, seqs in sorted(turns.items()):
            if len(seqs) < 2:
                continue
            agent_append = agent_rewrite = 0
            for (s0, m0), (s1, m1) in zip(seqs, seqs[1:]):
                total_pairs += 1
                d = first_diverge(m0, m1)
                if d is None:
                    append_pairs += 1
                    agent_append += 1
                    continue
                if isinstance(d, int) and d < len(m0):
                    # 真改写：旧历史中段被换（非纯追加）
                    rewrite_pairs += 1
                    agent_rewrite += 1
                    rewrites.append(
                        (agent_id[:8], s0, s1, d, len(m0), len(m1),
                         brief(m0[d], 120) if d < len(m0) else "",
                         brief(m1[d], 120) if d < len(m1) else "")
                    )
                else:
                    append_pairs += 1
                    agent_append += 1
            print(f"- agent={agent_id[:8]} turns={len(seqs)} "
                  f"append={agent_append} rewrite={agent_rewrite}")
        print(f"[{tag}] TOTAL pairs={total_pairs} append={append_pairs} "
              f"REWRITE={rewrite_pairs}")
        for r in rewrites[:8]:
            print(f"  REWRITE agent={r[0]} {r[1]}->{r[2]} @{r[3]} "
                  f"(len {r[4]}->{r[5]})")
            print(f"    prev: {r[6]}")
            print(f"    cur : {r[7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
