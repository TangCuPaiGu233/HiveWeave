"""团队开会 REST — 项目会议状态查询（docs/spec/team-meeting.md §前端/H）。

只读观察面：会议列表 + 进行中的会议（含 hold 名单）。人不能从 UI 开会，
这里不提供任何发起/干预端点。
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query

from hiveweave.services.meetings import hold
from hiveweave.services.meetings.service import meeting_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/meetings", tags=["meetings"])


def _camelize_meeting(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "chairId": row.get("chair_id"),
        "title": row.get("title"),
        "topics": row.get("topics") or [],
        "participants": row.get("participants") or [],
        "status": row.get("status"),
        "topicIndex": row.get("topic_index"),
        "roundIndex": row.get("round_index"),
        "topicResults": row.get("topic_results") or [],
        "deliveryState": row.get("delivery_state"),
        "holdStartedAt": row.get("hold_started_at"),
        "createdAt": row.get("created_at"),
        "concludedAt": row.get("concluded_at"),
    }


@router.get("")
async def list_meetings(
    project_id: str, limit: int = Query(default=20, ge=1, le=100)
) -> dict:
    """列出项目会议（人观察 / debug）。"""
    try:
        rows = await meeting_service.list_meetings(project_id, limit=limit)
    except Exception as e:
        log.warning("list_meetings_failed", project_id=project_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list meetings")
    return {"meetings": [_camelize_meeting(r) for r in rows]}


@router.get("/active")
async def get_active_meeting(project_id: str) -> dict:
    """当前进行中的会议 + hold 名单（前端会议状态条数据源）。"""
    try:
        row = await meeting_service.get_active_meeting(project_id)
    except Exception as e:
        log.warning("get_active_meeting_failed", project_id=project_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load meeting")
    return {
        "meeting": _camelize_meeting(row) if row else None,
        "heldAgentIds": sorted(hold.held_agent_ids(project_id)),
    }
