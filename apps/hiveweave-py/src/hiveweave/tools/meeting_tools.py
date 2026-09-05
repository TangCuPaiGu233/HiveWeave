"""团队开会工具 — start_team_meeting / speak_in_meeting / continue_meeting_round / conclude_topic。

权限模型（docs/spec/team-meeting.md §权限与工具）：
- ``start_team_meeting``：仅 ceo / coordinator。三层门：TOOL_CAPABILITY
  （MANAGE_ORG）→ policy 家族硬拒（HR 也有 MANAGE_ORG，必须家族门兜住）
  → 工具体内 family 复核（fail-closed）。
- ``speak_in_meeting`` / ``continue_meeting_round`` / ``conclude_topic``：
  **不进任何角色 allowlist** —— 只有 MeetingTurnRunner 执行期白名单能触发
  （runner 回调本地拦截，不落 executor）。普通路径调用到本文件函数体即
  fast-fail（规格 A 节：机制上不可达）。

``start_team_meeting`` 立即返回：先登记 hold，再 ``asyncio.create_task``
编排器 —— 工具协程里等待全员 IDLE = 自死锁（规格阻断项）。
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

import structlog

from hiveweave.tools.base import tool
from hiveweave.tools.result import ToolResult

log = structlog.get_logger(__name__)

MEETING_INVITE_HINT = (
    "start_team_meeting may only be called by the CEO or a mid-level "
    "coordinator (role_family=ceo|coordinator)"
)


class StartTeamMeetingParams(BaseModel):
    """Parameters for start_team_meeting."""

    model_config = {"populate_by_name": True}

    title: str = Field(
        description=(
            "Short meeting title (one line). Appears in the meeting status "
            "bar and in [MEETING RESULT] delivery."
        ),
    )
    topics: list[str] = Field(
        description=(
            "1..8 topics, discussed ONE BY ONE in order. Each topic gets at "
            "most 3 rounds; round 3 must conclude. Write each topic as a "
            "concrete question to decide, not a vague theme."
        ),
        json_schema_extra={"aliases": ["topic_list", "agenda"]},
    )
    participant_ids: list[str] = Field(
        default=None,
        description=(
            "Agent ids to invite. Cross-span invites are allowed; archived "
            "or foreign-project agents are rejected. You (the caller) attend "
            "as chair automatically and speak in every round. Minimum 2 "
            "attendees after adding yourself."
        ),
        validation_alias=AliasChoices("participant_ids", "participantIds",
                                      "participants", "attendees"),
        json_schema_extra={"aliases": ["participantIds", "participants",
                                       "attendees"]},
    )


class SpeakInMeetingParams(BaseModel):
    """Parameters for speak_in_meeting."""

    content: str = Field(
        description=(
            "Your statement for the current topic, from your own role's "
            "angle only. One speech per round; empty content is rejected "
            "and recorded as abstain."
        ),
    )


class ContinueMeetingRoundParams(BaseModel):
    """Parameters for continue_meeting_round."""

    direction: str = Field(
        description=(
            "Chair-only. Direction for the next round: what is still "
            "unresolved, which positions conflict, what attendees should "
            "address. Attendees see this direction instead of each other's "
            "raw speeches."
        ),
        json_schema_extra={"aliases": ["next_round_direction", "guidance"]},
    )


class ConcludeTopicParams(BaseModel):
    """Parameters for conclude_topic."""

    result: str = Field(
        description=(
            "Chair-only. The conclusion for the current topic — the single "
            "decision/summary that every attendee will receive in "
            "[MEETING RESULT]. The platform does not write conclusions for "
            "you; round 3 MUST conclude."
        ),
        json_schema_extra={"aliases": ["conclusion", "decision"]},
    )


@tool(
    "start_team_meeting",
    "Open a structured team meeting you chair (CEO/coordinator only). The "
    "platform acts as facilitator: attendees speak in PARALLEL BLIND "
    "review — nobody sees colleagues' raw speeches, only your written "
    "direction between rounds. Topics are discussed one by one, max 3 "
    "rounds each, round 3 must conclude. This call RETURNS IMMEDIATELY "
    "(status assembling); attendees are held until everyone is idle, then "
    "rounds run automatically. When every topic has a conclusion every "
    "attendee (including you) receives [MEETING RESULT]; the meeting "
    "process itself never enters personal memory. Do not dispatch tasks "
    "to attendees expecting answers mid-meeting — their replies are held. "
    "Only one meeting may run per project at a time.",
    requires_workspace=False,
    security_level="standard",
)
async def start_team_meeting_tool(
    params: StartTeamMeetingParams, agent_id: str, workspace: str, ctx=None
) -> ToolResult:
    """登记会议 + hold，立即返回（编排器后台等集合）。"""
    from hiveweave.db import meta as meta_db
    from hiveweave.services.meetings import orchestrator
    from hiveweave.services.meetings.service import (
        MeetingConflict,
        MeetingError,
    )
    from hiveweave.services.org import OrgService

    agent_row = await meta_db.get_agent_by_id(agent_id)
    if agent_row is None:
        return ToolResult.err(
            f"start_team_meeting: agent {agent_id[:12]} not found "
            f"(fail-closed; {MEETING_INVITE_HINT})"
        )
    from hiveweave.services.policy import infer_role_family

    family = infer_role_family(agent_row)
    if family not in ("ceo", "coordinator"):
        return ToolResult.err(
            f"{MEETING_INVITE_HINT}; got role_family={family}. Ask the CEO "
            f"or your coordinator to open the meeting."
        )
    project_id = str(agent_row.get("project_id") or "")
    if not project_id:
        return ToolResult.err("start_team_meeting: agent has no project")

    topics = [
        str(t or "").strip()
        for t in (params.topics or [])
        if str(t or "").strip()
    ]
    if not topics:
        return ToolResult.err(
            "start_team_meeting requires 1..8 non-empty topics"
        )
    if len(topics) > 8:
        return ToolResult.err(
            f"too many topics ({len(topics)}); split into multiple meetings "
            "(max 8 per meeting)"
        )

    org = OrgService()
    roster: list[str] = []
    for raw in params.participant_ids or []:
        pid = str(raw or "").strip()
        if not pid or pid in roster:
            continue
        row = await org.get_agent(pid)
        if row is None:
            return ToolResult.err(
                f"invitee not found: {pid[:12]} — copy public ids whole "
                "from tool receipts"
            )
        if (row.get("status") or "") in ("archived", "dismissed"):
            return ToolResult.err(
                f"invitee is archived/dismissed: {pid[:12]}"
            )
        if str(row.get("project_id") or "") != project_id:
            return ToolResult.err(
                f"invitee belongs to another project: {pid[:12]}"
            )
        roster.append(pid)

    try:
        meeting = await orchestrator.start_meeting(
            project_id=project_id,
            chair_id=agent_id,
            title=params.title or "",
            topics=topics,
            participant_ids=roster,
        )
    except MeetingConflict:
        return ToolResult.err(
            "another meeting is already in progress for this project — "
            "conclude or abort it first (one active meeting per project)"
        )
    except MeetingError as e:
        return ToolResult.err(f"start_team_meeting rejected: {e}")

    return ToolResult.ok(
        f"Meeting registered (id={meeting['id']}, status=assembling, "
        f"{len(topics)} topic(s), {len(meeting['participants'])} attendees). "
        "The orchestrator is assembling attendees now — it never cancels "
        "running work, busy attendees join when idle. Rounds run "
        "automatically: you speak in each round as a participant, then "
        "decide continue_meeting_round(direction) or conclude_topic(result) "
        "in your chair turn (round 3 must conclude). You will be woken "
        "[MEETING RESULT] when all topics conclude — your regular desk work "
        "is held meanwhile and resumes automatically.",
        meeting_id=meeting["id"],
        status=meeting["status"],
        topics=topics,
        participants=meeting["participants"],
    )


def _runner_only_reject(tool_name: str, doc: str) -> ToolResult:
    return ToolResult.err(
        f"{tool_name} is only available INSIDE a team-meeting turn "
        f"(MeetingTurnRunner whitelist). Normal turns cannot see or call it. "
        f"{doc}"
    )


@tool(
    "speak_in_meeting",
    "Submit your speech for the current meeting topic (meeting turns only). "
    "One speech per round; you cannot see other attendees' speeches — "
    "independent judgement is the point. After your speech is recorded the "
    "turn ends. Empty content is rejected and the turn becomes an abstain.",
    requires_workspace=False,
    security_level="standard",
)
async def speak_in_meeting_tool(
    params: SpeakInMeetingParams, agent_id: str, workspace: str, ctx=None
) -> ToolResult:
    """普通路径 fast-fail：真实记录在 runner 回调里本地拦截。"""
    return _runner_only_reject(
        "speak_in_meeting",
        "If you are in a meeting turn, call this tool with your statement.",
    )


@tool(
    "continue_meeting_round",
    "Chair-only, meeting facilitation turns only: inject the direction for "
    "the next round and reopen discussion (max 3 rounds per topic; "
    "unavailable at round 3 — conclude_topic instead). Attendees next see "
    "only: the topic, your direction, and already-concluded results.",
    requires_workspace=False,
    security_level="standard",
)
async def continue_meeting_round_tool(
    params: ContinueMeetingRoundParams, agent_id: str, workspace: str, ctx=None
) -> ToolResult:
    """普通路径 fast-fail：决策在 runner 回调里本地拦截。"""
    return _runner_only_reject(
        "continue_meeting_round",
        "If you are in a chair facilitation turn, call it with direction.",
    )


@tool(
    "conclude_topic",
    "Chair-only, meeting facilitation turns only: close the current topic "
    "with your conclusion. Available at any round (round 3 MUST use it). "
    "Concluding the LAST topic ends the meeting and delivers [MEETING "
    "RESULT] to every attendee.",
    requires_workspace=False,
    security_level="standard",
)
async def conclude_topic_tool(
    params: ConcludeTopicParams, agent_id: str, workspace: str, ctx=None
) -> ToolResult:
    """普通路径 fast-fail：决策在 runner 回调里本地拦截。"""
    return _runner_only_reject(
        "conclude_topic",
        "If you are in a chair facilitation turn, call it with result.",
    )
