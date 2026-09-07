# -*- coding: utf-8 -*-
"""直调 muse-spark-1.3 /responses（xhigh 思考），统计 SSE 事件类型。"""
import asyncio
import json
import sqlite3
import sys

import httpx

META = r"D:\PC_AI\Project\HiveWeave\apps\hiveweave-py\data\hiveweave.db"
MODEL_ROW_ID = "3b37728f-1912-4ae5-ab5c-3a0d178332aa"


def main() -> None:
    m = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    m.row_factory = sqlite3.Row
    r = dict(m.execute(
        "SELECT model_id, base_url, api_key FROM llm_models WHERE id=?",
        [MODEL_ROW_ID],
    ).fetchone())
    m.close()
    base, key, model = r["base_url"].rstrip("/"), r["api_key"], r["model_id"]
    print(f"model={model} base={base}")

    body = {
        "model": model,
        "input": "用一句话回答：1+1等于几？",
        "stream": True,
        "reasoning": {"effort": "xhigh"},
        "max_output_tokens": 2048,
    }
    counts: dict[str, int] = {}
    reasoning_sample: list[str] = []
    text_sample: list[str] = []

    async def run() -> None:
        async with httpx.AsyncClient(timeout=60, trust_env=True) as client:
            async with client.stream(
                "POST",
                f"{base}/responses",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as resp:
                print(f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    print((await resp.aread())[:500])
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        ev = json.loads(payload)
                    except Exception:
                        continue
                    etype = ev.get("type") or "?"
                    counts[etype] = counts.get(etype, 0) + 1
                    if "reasoning" in etype:
                        d = ev.get("delta") or ev.get("text") or ""
                        if d and len(reasoning_sample) < 3:
                            reasoning_sample.append(str(d)[:100])

    asyncio.run(run())
    print("\n=== 事件统计 ===")
    for k, v in sorted(counts.items()):
        print(f"{k}: {v}")
    print("\n=== reasoning 样本 ===")
    for s in reasoning_sample:
        print(s)


if __name__ == "__main__":
    sys.exit(main())
