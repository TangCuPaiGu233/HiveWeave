"""悬浮球 API + 静态托管 + 红点桥测试（spec §10 P0，打包税 #2）。

覆盖：
- 静态托管：HIVEWEAVE_BALL_STATIC_DIR 指向临时目录 → :4000 服务
  index.html/ball.js；目录缺失 → 内置兜底页（不 500）；路径穿越 → 404
- 红点桥：done/chat_message 出站 → 追踪面过滤 → 未读计数 + "ball" 频道
  ball_unread 事件；message_user（chat_message role=assistant）也推（§4.2
  不只镜像正文流）；同句短窗去重；clear_unread
- REST：/api/ball/state、/api/ball/chat（assistant 入口打桩）、
  /api/ball/unread、/api/ball/unread/clear
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hiveweave.realtime.event_bus import status_event_bus
from hiveweave.services import ball_bridge


@pytest.fixture(autouse=True)
def _reset_bridge():
    ball_bridge.reset_for_tests()
    yield
    ball_bridge.reset_for_tests()


@pytest.fixture
def ball_app(monkeypatch, tmp_path: Path) -> FastAPI:
    """最小 FastAPI app：只挂 ball 路由（不起 lifespan/全量路由）。"""
    from hiveweave.api.ball import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(ball_app) -> TestClient:
    return TestClient(ball_app)


# ── 静态托管 ─────────────────────────────────────────────────


def test_serves_index_and_assets_from_configured_dir(client, monkeypatch, tmp_path):
    static_dir = tmp_path / "ball"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<html><body>ball page</body></html>", encoding="utf-8"
    )
    (static_dir / "ball.js").write_text("window.__ball=1;", encoding="utf-8")
    monkeypatch.setenv("HIVEWEAVE_BALL_STATIC_DIR", str(static_dir))

    r1 = client.get("/ball")
    assert r1.status_code == 200
    assert "ball page" in r1.text
    assert "text/html" in r1.headers["content-type"]

    r2 = client.get("/ball/ball.js")
    assert r2.status_code == 200
    assert "window.__ball=1" in r2.text
    assert "javascript" in r2.headers["content-type"]


def test_fallback_page_when_dir_missing(client, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "HIVEWEAVE_BALL_STATIC_DIR", str(tmp_path / "no-such-dir")
    )
    r = client.get("/ball")
    assert r.status_code == 200
    assert "悬浮球" in r.text


def test_path_traversal_rejected(client, monkeypatch, tmp_path):
    static_dir = tmp_path / "ball"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    monkeypatch.setenv("HIVEWEAVE_BALL_STATIC_DIR", str(static_dir))

    r = client.get("/ball/%2e%2e%2fsecret.txt")
    assert r.status_code == 404
    assert "TOP SECRET" not in r.text


def test_missing_asset_is_404(client, monkeypatch, tmp_path):
    static_dir = tmp_path / "ball"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("HIVEWEAVE_BALL_STATIC_DIR", str(static_dir))
    assert client.get("/ball/nope.js").status_code == 404


def test_repo_static_assets_exist():
    """球页面三件套在仓库里自洽（apps/desktop/ball/）。"""
    root = Path(__file__).resolve().parents[3] / "apps" / "desktop" / "ball"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "ball.js").read_text(encoding="utf-8")
    css = (root / "ball.css").read_text(encoding="utf-8")
    assert 'src="/ball/ball.js"' in html and 'href="/ball/ball.css"' in html
    # §10 承重要素
    assert "data-drag-region" in html            # DRAG_REGION_SELECTOR 命中
    assert "DRAG_THRESHOLD_PX" in js             # 拖 vs 点阈值
    assert "/api/ball/chat" in js                # 入站走 deliver 管道
    assert "/api/ball/state" in js               # 标签 + 未读
    assert "source: \"ball\"" in js              # §4.1 source=ball
    assert "box-shadow" in css                   # F4 保底视觉（阴影）
    assert "border-radius" in css                # 不透明圆角


# ── 红点桥 ───────────────────────────────────────────────────


async def _drain(queue: asyncio.Queue) -> dict | None:
    try:
        return await asyncio.wait_for(queue.get(), timeout=2)
    except asyncio.TimeoutError:
        return None


async def test_done_event_increments_unread_and_publishes_ball_event():
    with patch(
        "hiveweave.services.ball_bridge._resolve_tracked",
        AsyncMock(return_value=["assistant-1"]),
    ):
        q = await status_event_bus.subscribe("ball")
        await ball_bridge._handle_bus_event(
            {"type": "done", "agentId": "assistant-1", "content": "回复正文"}
        )
        event = await _drain(q)
        await status_event_bus.unsubscribe(q)

    assert event is not None
    assert event["type"] == "ball_unread"
    assert event["agentId"] == "assistant-1"
    assert event["count"] == 1
    assert ball_bridge.get_unread_counts() == {"assistant-1": 1}


async def test_chat_message_assistant_role_also_pushes():
    """§4.2：不只镜像 message_user，也不只正文流——两类出站都推。"""
    with patch(
        "hiveweave.services.ball_bridge._resolve_tracked",
        AsyncMock(return_value=["ceo-1"]),
    ):
        await ball_bridge._handle_bus_event(
            {"type": "chat_message", "agentId": "ceo-1",
             "role": "assistant", "content": "message_user 的话"}
        )
        # user 角色的 chat_message 是入站，不推
        await ball_bridge._handle_bus_event(
            {"type": "chat_message", "agentId": "ceo-1",
             "role": "user", "content": "用户说的"}
        )
    assert ball_bridge.get_unread_counts() == {"ceo-1": 1}


async def test_untracked_agent_ignored():
    with patch(
        "hiveweave.services.ball_bridge._resolve_tracked",
        AsyncMock(return_value=["assistant-1"]),
    ):
        await ball_bridge._handle_bus_event(
            {"type": "done", "agentId": "random-worker", "content": "叶子的话"}
        )
    assert ball_bridge.get_unread_counts() == {}


async def test_short_window_dedupe_same_content():
    with patch(
        "hiveweave.services.ball_bridge._resolve_tracked",
        AsyncMock(return_value=["assistant-1"]),
    ):
        await ball_bridge._handle_bus_event(
            {"type": "done", "agentId": "assistant-1", "content": "同一句"}
        )
        await ball_bridge._handle_bus_event(
            {"type": "chat_message", "agentId": "assistant-1",
             "role": "assistant", "content": "同一句"}
        )
    assert ball_bridge.get_unread_counts() == {"assistant-1": 1}


async def test_empty_content_and_thinking_not_pushed():
    with patch(
        "hiveweave.services.ball_bridge._resolve_tracked",
        AsyncMock(return_value=["assistant-1"]),
    ):
        await ball_bridge._handle_bus_event(
            {"type": "done", "agentId": "assistant-1", "content": "   "}
        )
        await ball_bridge._handle_bus_event(
            {"type": "thinking", "agentId": "assistant-1", "content": "想"}
        )
        await ball_bridge._handle_bus_event(
            {"type": "text_delta", "agentId": "assistant-1", "content": "字"}
        )
    assert ball_bridge.get_unread_counts() == {}


async def test_clear_unread():
    with patch(
        "hiveweave.services.ball_bridge._resolve_tracked",
        AsyncMock(return_value=["a1", "a2"]),
    ):
        await ball_bridge._handle_bus_event(
            {"type": "done", "agentId": "a1", "content": "x"}
        )
        await ball_bridge._handle_bus_event(
            {"type": "done", "agentId": "a2", "content": "y"}
        )
    assert ball_bridge.clear_unread("a1") == 1
    assert ball_bridge.get_unread_counts() == {"a2": 1}
    assert ball_bridge.clear_unread() == 1
    assert ball_bridge.get_unread_counts() == {}


# ── REST ─────────────────────────────────────────────────────


def test_unread_rest_roundtrip(client):
    ball_bridge._note_unread("assistant-1", "你好")
    r = client.get("/api/ball/unread")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"assistant-1": 1}
    assert body["total"] == 1

    r2 = client.get("/api/ball/unread", params={"agentId": "assistant-1"})
    assert r2.json() == {"counts": {"assistant-1": 1}, "total": 1}
    r3 = client.get("/api/ball/unread", params={"agentId": "other"})
    assert r3.json() == {"counts": {"other": 0}, "total": 0}

    r4 = client.post("/api/ball/unread/clear", json={"agentId": "assistant-1"})
    assert r4.status_code == 200
    assert client.get("/api/ball/unread").json()["total"] == 0


def test_ball_chat_requires_content(client):
    r = client.post("/api/ball/chat", json={"content": "   "})
    assert r.status_code == 400


def test_ball_chat_routes_to_assistant_entry(client):
    captured: dict = {}

    async def fake_chat(content, *, source="ball"):
        captured["content"] = content
        captured["source"] = source
        return {"ok": True, "outcome": "started", "agentId": "A"}

    from hiveweave.services import assistant as assistant_mod

    with patch.object(assistant_mod, "chat_with_assistant", fake_chat):
        r = client.post(
            "/api/ball/chat",
            json={"content": "你好", "source": "ball"},
        )
    assert r.status_code == 200
    assert r.json()["outcome"] == "started"
    assert captured["content"] == "你好"
    assert captured["source"] == "ball"


def test_ball_chat_to_unknown_agent_is_404(client):
    r = client.post(
        "/api/ball/chat",
        json={"content": "hi", "agentId": "ghost-agent", "source": "ball"},
    )
    assert r.status_code == 404
