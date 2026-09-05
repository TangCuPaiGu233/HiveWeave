"""phoenix_adapter _handle_chat_push — 用户消息 images 透传 chat 落库。

回归：前端 WS chat push 带 images（string[]），后端保存用户消息时把图丢了
（REST 路径 api/chat.py 带 images，WS 路径落库无 images → 聊天记录看不到图）。
修复后所有用户消息落库点（idle 分支 + _save_user_and_ack：off-duty / busy
insert / busy queue）都与 REST 对齐，images list[str] 非空才透传。

已知限制（本次不扩展）：busy insert 的 agent.steer() 注入运行中 turn 仍只走
纯文本 {"from":"用户","content":...}——图会进聊天记录，但不会进 steer 窗口。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from hiveweave.realtime.phoenix_adapter import _handle_chat_push


def _make_idle_agent() -> SimpleNamespace:
    agent = SimpleNamespace(
        status=SimpleNamespace(value="idle"),
        project_id="p1",
        _on_stream_event=lambda *a, **k: None,  # 非空 → 跳过防御性回调补丁
        chat_calls=[],
    )

    async def chat(msg: str, *args, **kwargs) -> dict:
        # 用户发图喂 LLM（B8 后续）：idle 分支现在会带 opts={"images": [...]}
        # 触发 chat，签名需容忍该可选参数（详见 test_user_images_to_llm.py）。
        agent.chat_calls.append(msg)
        return {"ok": True}

    agent.chat = chat
    return agent


def _make_processing_agent(steer_result: dict) -> SimpleNamespace:
    agent = SimpleNamespace(
        status=SimpleNamespace(value="processing"),
        project_id="p1",
        _on_stream_event=lambda *a, **k: None,
        steer_calls=[],
    )

    async def steer(msg: str) -> dict:
        agent.steer_calls.append(msg)
        return steer_result

    agent.steer = steer
    return agent


async def _run_chat_push(payload: dict, agent: SimpleNamespace | None):
    """patch 掉 handler 的局部导入依赖，跑一遍 _handle_chat_push。

    返回 (save_message 收到的 dict 列表, 发出的 phoenix 帧)。
    """
    saved: list[dict] = []
    sent: list[list] = []

    class FakeChatService:
        async def save_message(self, attrs: dict) -> dict:
            saved.append(attrs)
            return {"id": f"m{len(saved)}"}

    class FakeManager:
        def __init__(self, a):
            self._a = a

        def get_agent(self, agent_id: str):
            return self._a

    import hiveweave.services.chat_message as chat_message_mod
    import hiveweave.agents.supervisor as supervisor_mod

    orig_chat_service = chat_message_mod.ChatMessageService
    orig_manager = supervisor_mod.agent_manager
    chat_message_mod.ChatMessageService = FakeChatService
    supervisor_mod.agent_manager = FakeManager(agent)
    try:
        async def send_fn(frame) -> bool:
            sent.append(frame)
            return True

        await _handle_chat_push("agent:agent-1", payload, send_fn)
    finally:
        chat_message_mod.ChatMessageService = orig_chat_service
        supervisor_mod.agent_manager = orig_manager

    return saved, sent


async def test_idle_chat_with_images_saves_images():
    """idle 分支：chat push 带 images → save_message 落库同一 images list。"""
    agent = _make_idle_agent()
    images = ["data:image/png;base64,AAA", "https://example.com/a.jpg"]
    saved, sent = await _run_chat_push(
        {"message": "看这张图", "images": images}, agent
    )

    assert len(saved) == 1
    assert saved[0]["role"] == "user"
    assert saved[0]["content"] == "看这张图"
    assert saved[0]["images"] == images
    # message_id ack 照发
    assert any(f[3] == "message_id" and f[4]["id"] == "m1" for f in sent)


async def test_idle_chat_without_images_saves_none():
    """回归：不带 images 的 push 行为与现状一致（images 落 None，不炸）。"""
    agent = _make_idle_agent()
    saved, sent = await _run_chat_push({"message": "纯文本"}, agent)

    assert len(saved) == 1
    assert saved[0]["images"] is None
    assert saved[0]["content"] == "纯文本"
    assert any(f[3] == "message_id" for f in sent)


async def test_idle_chat_bad_images_treated_as_none():
    """轻校验：非 list / 元素非 str / 空 list 一律按无图处理。"""
    agent = _make_idle_agent()
    for bad in ("not-a-list", [123, None], [], {"a": 1}):
        saved, _ = await _run_chat_push(
            {"message": "x", "images": bad}, agent
        )
        assert saved[-1]["images"] is None, f"bad images {bad!r} 应按 None 落库"


async def test_busy_insert_saves_images_but_steer_stays_text_only():
    """busy insert：图进聊天记录（save_message），steer 仍只注入纯文本。

    steer 已知限制声明用例：图片不进运行中 turn 的 next-step 窗口。
    """
    agent = _make_processing_agent({"ok": True})
    images = ["data:image/png;base64,BBB"]
    saved, sent = await _run_chat_push(
        {"message": "插话+图", "images": images, "mode": "insert"}, agent
    )

    assert len(saved) == 1
    assert saved[0]["images"] == images
    assert len(agent.steer_calls) == 1
    envelope = json.loads(agent.steer_calls[0])
    assert envelope["content"] == "插话+图"
    assert "images" not in envelope  # steer 语义只走文本
    assert any(f[3] == "inserted" for f in sent)
