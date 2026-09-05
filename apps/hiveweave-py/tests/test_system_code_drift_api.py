"""件1（42 轮报告 F14）：GET /api/system/code-drift — 指纹漂移 REST 出口。

现状：F14 出口只有 agent 工具 get_platform_state 告警与启动日志；本件补
一个只读 REST 端点（api/system.py），数据全部取自 code_fingerprint 现有
API。指纹未初始化时端点优雅返回 200 + drift=False（观测端点不比被观测
对象更脆——选 200 而非 503）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import hiveweave.services.code_fingerprint as cf


def _client() -> TestClient:
    from hiveweave.api.system import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_code_drift_endpoint_ok_and_schema(monkeypatch):
    """未初始化 → 200 + drift=False + reason；初始化后 → drift=True + 截断 20 条。"""
    client = _client()

    # ── 指纹未初始化：优雅 200，drift=False（选项 A，非 503）──
    monkeypatch.setattr(cf, "_startup_fingerprint", None)
    monkeypatch.setattr(cf, "_startup_snapshot", {})
    r = client.get("/api/system/code-drift")
    assert r.status_code == 200
    body = r.json()
    assert body["drift"] is False
    assert isinstance(body["checked_at"], str) and "T" in body["checked_at"]
    assert body["changed_count"] == 0
    assert body["changed_files"] == []
    assert "unavailable" in body["reason"]

    # ── 指纹已初始化（陈旧基线 vs 真实源码树）→ drift=True，列表截前 20 条 ──
    monkeypatch.setattr(cf, "_startup_fingerprint", "deadbeefdeadbeef")
    monkeypatch.setattr(cf, "_startup_snapshot", {"gone.py": (1, 2)})
    r2 = client.get("/api/system/code-drift")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["drift"] is True
    assert body2["changed_count"] >= 1
    assert isinstance(body2["changed_files"], list)
    assert len(body2["changed_files"]) <= 20
    assert all(isinstance(f, str) and f for f in body2["changed_files"])
