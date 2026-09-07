# -*- coding: utf-8 -*-
"""批次1取证 v2：从 s3-clone_10 真实库挖方言失败命令原文（只读）。"""
import sqlite3
import sys

DB = r"D:\PC_AI\Project\HiveTestProject\s3-clone_10\.hiveweave\data.db"


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, run_id, tool_name, tool_args_excerpt, status, "
            "result_excerpt, error, runner_failed, command_failed "
            "FROM run_steps WHERE runner_failed=1 ORDER BY started_at"
        ).fetchall()
        print(f"runner_failed=1 total={len(rows)}")
        for r in rows:
            print("=" * 76)
            print(f"tool={r['tool_name']} status={r['status']}")
            print("ARGS:", (r["tool_args_excerpt"] or "")[:500])
            err = (r["error"] or "")
            res = (r["result_excerpt"] or "")[:300]
            print("ERROR:", err[:300])
            print("RESULT:", res)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
