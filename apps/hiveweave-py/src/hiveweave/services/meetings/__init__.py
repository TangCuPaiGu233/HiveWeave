"""团队开会（过程遗忘、只记结论）— docs/spec/team-meeting.md。

平台当会务组：盲评并行发言 + 主席注入方向；散会只投 [MEETING RESULT]，
过程不进个人记忆。包结构：

- ``service``       行状态、议题/轮次、唯一约束
- ``hold``          park-all、is_held、may_start_org_turn
- ``orchestrator``  集合等待、fan-out、超时、回岗、泵
- ``runner``        MeetingTurnRunner（自建 Streamer，不走 agent.chat）
- ``prompts``       结构化简报（纯函数 + 金丝雀）
"""

from hiveweave.services.meetings.hold import (
    apply_hold,
    is_held,
    may_start_org_turn,
    park_all_pending_wakes,
    release_hold,
)
from hiveweave.services.meetings.service import (
    MAX_ROUNDS,
    ACTIVE_STATUSES,
    MeetingConflict,
    MeetingError,
    meeting_service,
)
from hiveweave.services.meetings.prompts import (
    MEETING_ABORTED_TAG,
    MEETING_RESULT_TAG,
)

__all__ = [
    "MAX_ROUNDS",
    "ACTIVE_STATUSES",
    "MEETING_ABORTED_TAG",
    "MEETING_RESULT_TAG",
    "MeetingConflict",
    "MeetingError",
    "apply_hold",
    "is_held",
    "may_start_org_turn",
    "meeting_service",
    "park_all_pending_wakes",
    "release_hold",
]
