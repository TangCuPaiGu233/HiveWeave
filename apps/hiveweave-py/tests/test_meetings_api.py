"""团队开会 — API / 导出面（docs/spec/team-meeting.md §规格验收 H）。"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from hiveweave.services.meetings.service import meeting_service
from tests.meeting_env import insert_agent, meeting_env

PID = "mtg-api-1"


async def _client():
    from hiveweave.api.meetings import router as meetings_router

    app = FastAPI()
    app.include_router(meetings_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_meetings_rest_active_and_list():
    async with meeting_env(PID) as env:
        pid = env["project_id"]
        for aid in ("chair-1", "p-1"):
            await insert_agent(pid, aid)
        async with await _client() as client:
            # 初始无会议
            res = await client.get(f"/api/projects/{pid}/meetings/active")
            assert res.status_code == 200
            assert res.json()["meeting"] is None
            # 建会（assembling）→ active 返回 camelCase 状态
            m = await meeting_service.create_meeting(
                pid, "chair-1", "api", ["t1"], ["p-1"]
            )
            res = await client.get(f"/api/projects/{pid}/meetings/active")
            body = res.json()
            assert body["meeting"]["id"] == m["id"]
            assert body["meeting"]["status"] == "assembling"
            assert body["meeting"]["topicIndex"] == 0
            assert body["meeting"]["deliveryState"] == "none"
            # list 包含该会议
            res = await client.get(f"/api/projects/{pid}/meetings")
            rows = res.json()["meetings"]
            assert any(r["id"] == m["id"] for r in rows)


async def test_export_tables_include_meetings():
    """H：project-export 默认表集合含 meetings / meeting_utterances。"""
    from hiveweave.api.debug import _EXPORT_TABLES

    assert "meetings" in _EXPORT_TABLES
    assert "meeting_utterances" in _EXPORT_TABLES
