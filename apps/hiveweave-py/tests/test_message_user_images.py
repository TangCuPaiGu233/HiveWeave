"""件3（agent→用户发图 2026-09-05）单元测试：message_user 的 images 参数。

落库路径取证：message_user 直接写 chat_messages（用户 Chat 面板消息源），
不经 inbox 中转 —— images 直接落该消息的 images 列（前端 MessageBubble
已支持渲染），并同步进 WebSocket 推送。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hiveweave.tools.misc_tools import MessageUserParams, message_user_tool

_SMALL_IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
_SMALL_IMG2 = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


@pytest.fixture
def _msg_env():
    saved = AsyncMock(return_value={"id": "m1"})
    chat_cls = MagicMock()
    chat_cls.return_value.save_message = saved
    bus = MagicMock()
    bus.publish_chat_message = AsyncMock()
    with patch(
        "hiveweave.services.chat_message.ChatMessageService", chat_cls
    ), patch(
        "hiveweave.tools.misc_tools._ceo_exit_assertion_block",
        AsyncMock(return_value=None),
    ), patch(
        "hiveweave.realtime.event_bus.status_event_bus", bus
    ):
        yield saved, bus


@pytest.mark.asyncio
async def test_images_land_on_user_visible_chat_message(_msg_env):
    saved, bus = _msg_env
    result = await message_user_tool(
        MessageUserParams(message="效果图如下", images=[_SMALL_IMG, _SMALL_IMG2]),
        "ceo-uuid",
        "/ws",
        None,
    )
    assert result.success is True, result.error
    assert saved.await_count == 1
    payload = saved.call_args[0][0]
    # 落库到用户可见的 chat_messages：images 列 + 来源标记
    assert payload["images"] == [_SMALL_IMG, _SMALL_IMG2]
    assert payload["metadata"] == {"source": "agent_to_user"}
    assert payload["role"] == "assistant"
    assert payload["content"] == "效果图如下"
    # WebSocket 实时推送同样带图
    _, kwargs = bus.publish_chat_message.call_args
    assert kwargs["message"]["images"] == [_SMALL_IMG, _SMALL_IMG2]


@pytest.mark.asyncio
async def test_bare_string_image_coerced_to_list(_msg_env):
    saved, _bus = _msg_env
    params = MessageUserParams(message="截图", images=_SMALL_IMG)  # 裸字符串
    assert params.images == [_SMALL_IMG]
    result = await message_user_tool(params, "ceo-uuid", "/ws", None)
    assert result.success is True, result.error
    payload = saved.call_args[0][0]
    assert payload["images"] == [_SMALL_IMG]


@pytest.mark.asyncio
async def test_too_many_images_rejected_with_remedy(_msg_env):
    saved, _bus = _msg_env
    result = await message_user_tool(
        MessageUserParams(
            message="太多图", images=[_SMALL_IMG] * 6
        ),
        "ceo-uuid",
        "/ws",
        None,
    )
    assert result.success is not True
    err = str(result.error or "")
    assert "5 张上限" in err and "分多条" in err  # 处方
    saved.assert_not_called()


@pytest.mark.asyncio
async def test_oversized_image_rejected_with_remedy(_msg_env):
    saved, _bus = _msg_env
    big = "data:image/png;base64," + "A" * 2_800_001
    result = await message_user_tool(
        MessageUserParams(message="巨图", images=[big]),
        "ceo-uuid",
        "/ws",
        None,
    )
    assert result.success is not True
    err = str(result.error or "")
    assert "2MB" in err and "压缩" in err  # 处方
    saved.assert_not_called()


# ── 备注②：前缀白名单（data:image/ 或 http(s)://）──────


@pytest.mark.asyncio
async def test_bare_base64_without_data_prefix_rejected(_msg_env):
    """裸 base64（缺 data:image/ 前缀）→ 拒绝并给补前缀处方。"""
    saved, _bus = _msg_env
    result = await message_user_tool(
        MessageUserParams(message="裸base64", images=["iVBORw0KGgoAAAANSUhEUg=="]),
        "ceo-uuid",
        "/ws",
        None,
    )
    assert result.success is not True
    err = str(result.error or "")
    assert "data:image/" in err and "处方" in err
    saved.assert_not_called()


@pytest.mark.asyncio
async def test_local_path_image_rejected(_msg_env):
    saved, _bus = _msg_env
    result = await message_user_tool(
        MessageUserParams(message="本地路径", images=["C:\\Users\\pics\\a.png"]),
        "ceo-uuid",
        "/ws",
        None,
    )
    assert result.success is not True
    assert "http" in str(result.error or "")
    saved.assert_not_called()


@pytest.mark.asyncio
async def test_https_image_url_accepted(_msg_env):
    saved, bus = _msg_env
    url = "https://example.com/render.png"
    result = await message_user_tool(
        MessageUserParams(message="线上效果图", images=[url]),
        "ceo-uuid",
        "/ws",
        None,
    )
    assert result.success is True, result.error
    payload = saved.call_args[0][0]
    assert payload["images"] == [url]
    _, kwargs = bus.publish_chat_message.call_args
    assert kwargs["message"]["images"] == [url]


@pytest.mark.asyncio
async def test_no_images_regression(_msg_env):
    """不带图回归：payload 不含 images/metadata 键（既有纯文本路径不变）。"""
    saved, bus = _msg_env
    result = await message_user_tool(
        MessageUserParams(message="普通汇报"), "ceo-uuid", "/ws", None
    )
    assert result.success is True, result.error
    payload = saved.call_args[0][0]
    assert "images" not in payload
    assert "metadata" not in payload
    _, kwargs = bus.publish_chat_message.call_args
    assert "images" not in kwargs["message"]
