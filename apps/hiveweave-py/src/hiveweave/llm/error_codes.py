"""Provider-neutral 稳定错误码表（批次 D 结构位 #1）。

DSH 教条：一个 ``code: str`` 字段让重试/熔断/剥图/审计四个消费方全部
解耦于 provider 文案。HiveWeave 此前在 retry.py（状态码+正则）、
circuit_breaker.py（熔断）、provider.py（剥图负缓存）各自匹配状态码
与文案——本模块统一收口。

用法：provider 层抛出/返回错误时调用 ``classify_error()`` 得到稳定码；
消费方（retry/circuit/UI）按码路由，不再匹配状态码或文案。
"""

from __future__ import annotations

import re
from enum import Enum


class ErrorCode(str, Enum):
    """Provider-neutral 稳定错误码（对齐 DSH error.ts 码集）。"""

    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER = "SERVER"
    TIMEOUT = "TIMEOUT"
    TRANSPORT = "TRANSPORT"
    QUOTA = "QUOTA"
    CONTEXT_WINDOW = "CONTEXT_WINDOW_EXCEEDED"
    INVALID_REQUEST = "INVALID_REQUEST"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    UNKNOWN = "UNKNOWN"


#: 429 优先级低于 QUOTA——quota 文案命中时即便状态码 429 也归 QUOTA
_QUOTA_RE = re.compile(
    r"quota|insufficient.*balance|billing|余额不足|欠费", re.IGNORECASE,
)
_CONTEXT_RE = re.compile(
    r"context.*(length|window|overflow)|maximum.*tokens|too many.*tokens|"
    r"prompt.*too.*long|input.*exceeds",
    re.IGNORECASE,
)


def classify_error(
    status: int | None = None,
    body: str = "",
) -> ErrorCode:
    """按 HTTP 状态码 + 响应体文案分类为稳定错误码。

    分类优先级：QUOTA 文案 > RATE_LIMIT(429) > AUTH(401/403) >
    CONTEXT(400+文案) > SERVER(5xx) > INVALID_REQUEST(4xx) > UNKNOWN。
    """
    if status == 402 or (body and _QUOTA_RE.search(body)):
        return ErrorCode.QUOTA
    if status == 429:
        return ErrorCode.RATE_LIMIT
    if status in (401, 403):
        return ErrorCode.AUTH
    if status == 413:
        return ErrorCode.INVALID_REQUEST
    if status is not None and 400 <= status < 500:
        if body and _CONTEXT_RE.search(body):
            return ErrorCode.CONTEXT_WINDOW
        return ErrorCode.INVALID_REQUEST
    if status is not None and status >= 500:
        return ErrorCode.SERVER
    if status is None and body:
        # 流内错误（HTTP 200 但 body 含 error）——按文案分类
        if _CONTEXT_RE.search(body):
            return ErrorCode.CONTEXT_WINDOW
        if _QUOTA_RE.search(body):
            return ErrorCode.QUOTA
    return ErrorCode.UNKNOWN


def is_retryable_code(code: ErrorCode) -> bool:
    """稳定码→可重试判定（取代散落的状态码+正则匹配）。"""
    return code in (
        ErrorCode.RATE_LIMIT,
        ErrorCode.SERVER,
        ErrorCode.TIMEOUT,
        ErrorCode.TRANSPORT,
        ErrorCode.EMPTY_RESPONSE,
    )
