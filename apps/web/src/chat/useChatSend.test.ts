import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef } from "react";
import { useChatSend } from "./useChatSend";
import { streamChat } from "../api";
import { useAppStore } from "../store";
import type { ChatMessage, StreamDraft } from "./types";
import type { ChatEvent } from "../api/ws";

vi.mock("../api", () => ({
  streamChat: vi.fn(() => ({ abort: vi.fn() })),
  joinAgentChannel: vi.fn(() => Promise.resolve()),
}));

type HarnessProps = {
  agentId: string | null;
  isStreaming: boolean;
  isAgentProcessing: boolean;
};

/**
 * Minimal harness mirroring ChatPanel's wiring: stable refs, agentId as a prop
 * (ChatPanel is NOT remounted on agent switch — same hook instance survives).
 */
function useHarness(props: HarnessProps) {
  const activeAgentIdRef = useRef<string | null>(props.agentId);
  activeAgentIdRef.current = props.agentId;
  const streamDraftRef = useRef<StreamDraft | null>(null);
  const stickToBottomRef = useRef(true);
  return useChatSend({
    agentId: props.agentId,
    activeAgentIdRef,
    streamDraftRef,
    updateStreamDraft: () => {},
    isStreaming: props.isStreaming,
    setIsStreaming: () => {},
    isAgentProcessing: props.isAgentProcessing,
    loadMessagesFromDb: vi.fn(async () => true),
    setMessages: () => {},
    refreshOrgTree: () => {},
    thinkingElapsed: null,
    setThinkingElapsed: () => {},
    stickToBottomRef,
  });
}

describe("useChatSend — per-agent send queue", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(streamChat).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("never auto-sends a queued message to a different agent after switching chats", () => {
    const { result, rerender } = renderHook((p: HarnessProps) => useHarness(p), {
      initialProps: { agentId: "A", isStreaming: true, isAgentProcessing: true },
    });

    // Agent A is busy → message is parked in the queue.
    act(() => result.current.setInput("把团队扩散一下"));
    act(() => result.current.handleSend());
    expect(streamChat).not.toHaveBeenCalled();
    expect(result.current.queuedCount).toBe(1);

    // Switch to idle agent B — the old bug drained A's queue into B here.
    rerender({ agentId: "B", isStreaming: false, isAgentProcessing: false });
    expect(streamChat).not.toHaveBeenCalled();
    // Banner counts only the viewed agent's entries.
    expect(result.current.queuedCount).toBe(0);

    // Switch back to A, now idle → the parked message drains to A only.
    rerender({ agentId: "A", isStreaming: false, isAgentProcessing: false });
    expect(streamChat).toHaveBeenCalledTimes(1);
    expect(vi.mocked(streamChat).mock.calls[0][0]).toBe("A");
    expect(vi.mocked(streamChat).mock.calls[0][1]).toBe("把团队扩散一下");
    expect(result.current.queuedCount).toBe(0);
  });

  it("handleStop clears only the viewed agent's queued entries", () => {
    const { result, rerender } = renderHook((p: HarnessProps) => useHarness(p), {
      initialProps: { agentId: "A", isStreaming: true, isAgentProcessing: true },
    });

    // Park one message for A (busy) and one for B (busy).
    act(() => result.current.setInput("msg for A"));
    act(() => result.current.handleSend());
    rerender({ agentId: "B", isStreaming: true, isAgentProcessing: true });
    act(() => result.current.setInput("msg for B"));
    act(() => result.current.handleSend());
    expect(result.current.queuedCount).toBe(1);
    expect(streamChat).not.toHaveBeenCalled();

    // Stop on B clears B's entry only; A's entry stays parked.
    act(() => result.current.handleStop());
    expect(result.current.queuedCount).toBe(0);

    rerender({ agentId: "A", isStreaming: true, isAgentProcessing: true });
    expect(result.current.queuedCount).toBe(1);
    expect(streamChat).not.toHaveBeenCalled();
  });

  it("delayed resend timer stays parked when the user switched chats within 300ms", () => {
    let onEvent: (event: ChatEvent) => void = () => {};
    vi.mocked(streamChat).mockImplementation((_id, _msg, _imgs, cb) => {
      onEvent = cb;
      return { abort: vi.fn() };
    });

    const { result, rerender } = renderHook((p: HarnessProps) => useHarness(p), {
      initialProps: { agentId: "A", isStreaming: false, isAgentProcessing: false },
    });

    // First message sends immediately; a second one is parked while A streams.
    act(() => result.current.setInput("first"));
    act(() => result.current.handleSend());
    expect(streamChat).toHaveBeenCalledTimes(1);
    rerender({ agentId: "A", isStreaming: true, isAgentProcessing: true });
    act(() => result.current.setInput("second"));
    act(() => result.current.handleSend());
    expect(result.current.queuedCount).toBe(1);

    // A's stream completes → 300ms resend timer armed; user switches to B.
    act(() => onEvent({ type: "done", data: "" }));
    rerender({ agentId: "B", isStreaming: false, isAgentProcessing: false });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(streamChat).toHaveBeenCalledTimes(1);

    // Back on idle A, the parked message drains to A.
    rerender({ agentId: "A", isStreaming: false, isAgentProcessing: false });
    expect(streamChat).toHaveBeenCalledTimes(2);
    expect(vi.mocked(streamChat).mock.calls[1][0]).toBe("A");
    expect(vi.mocked(streamChat).mock.calls[1][1]).toBe("second");
  });
});

describe("useChatSend — round_start", () => {
  beforeEach(() => {
    vi.mocked(streamChat).mockClear();
  });

  it("keeps prior narration/tool chips and appends a round_boundary segment", () => {
    let onEvent: (event: ChatEvent) => void = () => {};
    vi.mocked(streamChat).mockImplementation((_id, _msg, _imgs, cb) => {
      onEvent = cb;
      return { abort: vi.fn() };
    });

    let draft: StreamDraft | null = null;
    function useRoundHarness() {
      const activeAgentIdRef = useRef<string | null>("A");
      const streamDraftRef = useRef<StreamDraft | null>(null);
      const stickToBottomRef = useRef(true);
      const updateStreamDraft = (
        updater: StreamDraft | null | ((prev: StreamDraft | null) => StreamDraft | null)
      ) => {
        draft = typeof updater === "function" ? updater(draft) : updater;
        streamDraftRef.current = draft;
      };
      return useChatSend({
        agentId: "A",
        activeAgentIdRef,
        streamDraftRef,
        updateStreamDraft,
        isStreaming: false,
        setIsStreaming: () => {},
        isAgentProcessing: false,
        loadMessagesFromDb: vi.fn(async () => true),
        setMessages: () => {},
        refreshOrgTree: () => {},
        thinkingElapsed: null,
        setThinkingElapsed: () => {},
        stickToBottomRef,
      });
    }

    const { result } = renderHook(() => useRoundHarness());
    act(() => result.current.setInput("go"));
    act(() => result.current.handleSend());
    expect(streamChat).toHaveBeenCalledTimes(1);

    act(() => {
      draft = {
        assistantId: "m1",
        segments: [
          { type: "thinking", content: "plan" },
          { type: "text", content: "用户选了方案2" },
          { type: "tool_call", tool: { tool: "get_tasks", input: {} } },
        ],
      };
    });
    act(() => onEvent({ type: "round_start", data: "1" }));
    const finalDraft = draft as StreamDraft | null;
    // 契约（B6 渲染统一 + round 号解析补齐）：round_start 不丢弃前轮
    // 旁白/thinking，尾部插入 round_boundary 段——与被动路径
    // （useChatMessages）同款，用户发起的 turn live 阶段也有轮次分隔。
    expect(finalDraft?.segments).toEqual([
      { type: "thinking", content: "plan" },
      { type: "text", content: "用户选了方案2" },
      { type: "tool_call", tool: { tool: "get_tasks", input: {} } },
      { type: "round_boundary", round: 1 },
    ]);
  });
});

// TEST_DSH_44 Bug#1：一个 turn 有两次 done——streamer 收尾 done（早于
// handle_completion 落库 metadata.segments）+ completion 落库后的权威 done。
// 第一次到达时 DB 快照无 segments，直接清 draft 会把整轮结构化渲染坍缩成
// 平铺文本。契约：gate（settledMessageHasSegments）失败保留 persisted
// draft，权威 done / 带segments 快照落地后才清。
describe("useChatSend — done 收口 gate（TEST_DSH_44 Bug#1）", () => {
  const noSegmentsRow: ChatMessage = {
    id: "m1",
    role: "assistant",
    content: "中途旁白拼接平文本",
    timestamp: 1,
    isStreaming: true,
  };
  const withSegmentsRow: ChatMessage = {
    id: "m1",
    role: "assistant",
    content: "中途旁白拼接平文本",
    timestamp: 1,
    _segments: [
      { type: "text", content: "第一轮旁白" },
      { type: "round_boundary", round: 1 },
      { type: "text", content: "第二轮旁白" },
    ],
  };

  function mountGateHarness() {
    let onEvent: (event: ChatEvent) => void = () => {};
    vi.mocked(streamChat).mockImplementation((_id, _msg, _imgs, cb) => {
      onEvent = cb;
      return { abort: vi.fn() };
    });
    let draft: StreamDraft | null = null;
    function useHarness() {
      const activeAgentIdRef = useRef<string | null>("A");
      const streamDraftRef = useRef<StreamDraft | null>(null);
      const stickToBottomRef = useRef(true);
      const updateStreamDraft = (
        updater: StreamDraft | null | ((prev: StreamDraft | null) => StreamDraft | null)
      ) => {
        draft = typeof updater === "function" ? updater(draft) : updater;
        streamDraftRef.current = draft;
      };
      return useChatSend({
        agentId: "A",
        activeAgentIdRef,
        streamDraftRef,
        updateStreamDraft,
        isStreaming: false,
        setIsStreaming: () => {},
        isAgentProcessing: false,
        loadMessagesFromDb: vi.fn(async () => true),
        setMessages: () => {},
        refreshOrgTree: () => {},
        thinkingElapsed: null,
        setThinkingElapsed: () => {},
        stickToBottomRef,
      });
    }
    // 单次 mount；后续通过 result 驱动（多次 renderHook 会建多套闭包）
    const rendered = renderHook(() => useHarness());
    return {
      result: rendered.result,
      fire: (event: ChatEvent) => onEvent(event),
      getDraft: () => draft,
    };
  }

  function startTurn(h: ReturnType<typeof mountGateHarness>) {
    act(() => h.result.current.setInput("go"));
    act(() => h.result.current.handleSend());
    act(() =>
      h.fire({
        type: "message_id",
        data: JSON.stringify({ role: "assistant", id: "m1" }),
      })
    );
    act(() => h.fire({ type: "text_delta", data: "第一轮旁白" }));
    expect(h.getDraft()?.assistantId).toBe("m1");
  }

  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.getState().clearChatSessions();
    vi.mocked(streamChat).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    useAppStore.getState().clearChatSessions();
  });

  it("done 时快照无 segments → draft 保留 persisted 不清空（重试耗尽也不坍缩）", async () => {
    const h = mountGateHarness();
    startTurn(h);

    useAppStore.getState().setChatMessages("A", [noSegmentsRow]);
    act(() => h.fire({ type: "done", data: "" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    // done#1（streamer 收尾，segments 未落库）：draft 切 persisted 留屏
    expect(h.getDraft()).not.toBeNull();
    expect(h.getDraft()?.persisted).toBe(true);

    // 5×400ms 重试耗尽，快照始终无 segments → 不清 draft（旧实现此处坍缩）
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });
    }
    expect(h.getDraft()).not.toBeNull();
    expect(h.getDraft()?.persisted).toBe(true);
  });

  it("权威快照带 segments（第二次 done）→ 正常 swap 清 draft 回归", async () => {
    const h = mountGateHarness();
    startTurn(h);

    useAppStore.getState().setChatMessages("A", [noSegmentsRow]);
    act(() => h.fire({ type: "done", data: "" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(h.getDraft()).not.toBeNull();

    // 权威 done：completion 已把 metadata.segments 落库
    useAppStore.getState().setChatMessages("A", [withSegmentsRow]);
    act(() => h.fire({ type: "done", data: "" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(h.getDraft()).toBeNull();
  });

  it("done 时快照已带 segments → 单次 done 即 swap（正常路径回归）", async () => {
    const h = mountGateHarness();
    startTurn(h);

    useAppStore.getState().setChatMessages("A", [withSegmentsRow]);
    act(() => h.fire({ type: "done", data: "" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(h.getDraft()).toBeNull();
  });

  it("swap 重试窗口跨新 turn：旧 swap 终态不翻转/不清新 turn 的 live draft（P2-1）", async () => {
    const h = mountGateHarness();
    startTurn(h); // message_id m1 + text_delta

    useAppStore.getState().setChatMessages("A", [noSegmentsRow]);
    act(() => h.fire({ type: "done", data: "" })); // done#1 → m1 persisted，重试排程
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(h.getDraft()?.assistantId).toBe("m1");
    expect(h.getDraft()?.persisted).toBe(true);

    // done#1 之后用户立即手发下一条：新 turn 的 message_id(m2) 重建 draft
    // （主动路径 message_id 无条件重建；id 守卫同时把 done 翻转的目标
    // 切到 m2 —— 旧 turn 的 swap 链仍闭包在 m1 上）
    act(() =>
      h.fire({ type: "message_id", data: JSON.stringify({ role: "assistant", id: "m2" }) })
    );
    act(() => h.fire({ type: "text_delta", data: "新 turn 旁白" }));
    expect(h.getDraft()?.assistantId).toBe("m2");
    expect(h.getDraft()?.persisted).toBeUndefined();

    // 旧 turn（m1）的 swap 重试耗尽 → 终态不得翻转/清空 m2 的 live draft
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });
    }
    expect(h.getDraft()).not.toBeNull();
    expect(h.getDraft()?.assistantId).toBe("m2");
    expect(h.getDraft()?.persisted).toBeUndefined();
    // 新 turn 的流式内容未丢
    expect(
      h.getDraft()?.segments.some((s) => s.type === "text" && s.content === "新 turn 旁白")
    ).toBe(true);
  });
});
