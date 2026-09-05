import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useCallback, useRef, useState } from "react";
import { useChatMessages } from "./useChatMessages";
import { useAppStore } from "../store";
import type { ChatMessage, StreamDraft } from "./types";
import { getAgent, getChatMessages, markMessagesRead, subscribeAgentStream } from "../api";

vi.mock("../api", () => ({
  getAgent: vi.fn(async (id: string) => ({ id, name: id, role: "ceo", status: "idle" })),
  getChatMessages: vi.fn(async () => []),
  markMessagesRead: vi.fn(async () => ({})),
  subscribeAgentStream: vi.fn(() => () => {}),
}));

function useHarness(agentId: string | null) {
  const activeAgentIdRef = useRef<string | null>(agentId);
  activeAgentIdRef.current = agentId;
  const streamDraftRef = useRef<StreamDraft | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const updateStreamDraft = useCallback(() => {}, []);
  const setThinkingElapsed = useCallback(() => {}, []);
  return useChatMessages({
    agentId,
    streamDraft: null,
    streamDraftRef,
    updateStreamDraft,
    isStreaming,
    setIsStreaming,
    setThinkingElapsed,
    activeAgentIdRef,
  });
}

const letterA: ChatMessage = {
  id: "t-a",
  role: "team",
  content: "A letter",
  timestamp: 1,
  isRead: true,
};

const dbLetterA = {
  id: "t-a",
  role: "team",
  content: "A letter",
  created_at: 1,
  isRead: true,
  isBackground: true,
};

describe("useChatMessages agent switch cache", () => {
  beforeEach(() => {
    useAppStore.getState().clearChatSessions();
    vi.mocked(getChatMessages).mockReset();
    vi.mocked(getChatMessages).mockResolvedValue([]);
    vi.mocked(getAgent).mockClear();
    vi.mocked(markMessagesRead).mockClear();
    vi.mocked(subscribeAgentStream).mockClear();
  });

  afterEach(() => {
    useAppStore.getState().clearChatSessions();
  });

  it("keeps A's 团队沟通 while B is still loading, then restores A on switch back", async () => {
    useAppStore.getState().setChatMessages("A", [letterA]);
    let resolveB: (rows: unknown[]) => void = () => {};
    const pendingB = new Promise<unknown[]>((resolve) => {
      resolveB = resolve;
    });
    vi.mocked(getChatMessages).mockImplementation(async (id: string) => {
      if (id === "A") return [dbLetterA];
      return pendingB;
    });

    const { rerender, result } = renderHook(({ id }) => useHarness(id), {
      initialProps: { id: "A" },
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.teamMessages.some((m) => m.id === "t-a")).toBe(true);

    act(() => {
      rerender({ id: "B" });
    });
    expect(useAppStore.getState().chatSessions.A?.some((m) => m.id === "t-a")).toBe(true);
    expect(useAppStore.getState().chatSessions.B?.some((m) => m.id === "t-a")).toBeFalsy();
    expect(result.current.teamMessages.some((m) => m.id === "t-a")).toBe(false);

    await act(async () => {
      resolveB([]);
      await Promise.resolve();
    });
    expect(useAppStore.getState().chatSessions.A?.some((m) => m.id === "t-a")).toBe(true);

    await act(async () => {
      rerender({ id: "A" });
      await Promise.resolve();
    });
    expect(result.current.teamMessages.some((m) => m.id === "t-a")).toBe(true);
  });

  it("does not empty A's transcript when org tree refreshes", async () => {
    useAppStore.getState().setChatMessages("A", [letterA]);
    vi.mocked(getChatMessages).mockResolvedValue([dbLetterA]);
    const { result } = renderHook(({ id }) => useHarness(id), { initialProps: { id: "A" } });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.teamMessages.some((m) => m.id === "t-a")).toBe(true);

    await act(async () => {
      useAppStore.getState().refreshOrgTree();
      await Promise.resolve();
    });
    expect(result.current.teamMessages.some((m) => m.id === "t-a")).toBe(true);
    expect(useAppStore.getState().chatSessions.A?.some((m) => m.id === "t-a")).toBe(true);
  });
});

// WS 重连对账：断连窗口内错过的 done 已成过去（replay 被挤出/后端重启）
// 时，重连必须主动拉一次 DB 对账；agent 已不在处理中时冻结 draft 让位给
// DB 行，否则面板永远停在断连时刻的中途快照上。
describe("useChatMessages — socket reconnect reconcile", () => {
  function useReconcileHarness(
    agentId: string,
    draftRef: { current: StreamDraft | null },
    nullWrites: () => void,
  ) {
    const activeAgentIdRef = useRef<string | null>(agentId);
    activeAgentIdRef.current = agentId;
    const [isStreaming, setIsStreaming] = useState(false);
    // spy 身份稳定（vi.fn）；否则 updateStreamDraft 每渲染换引用，
    // 对账 effect 的 cleanup 会在 fetch 往返前把 cancelled 置真。
    const nullWritesRef = useRef(nullWrites);
    nullWritesRef.current = nullWrites;
    const updateStreamDraft = useCallback(
      (u: any) => {
        const next = typeof u === "function" ? u(draftRef.current) : u;
        if (next === null) nullWritesRef.current();
        draftRef.current = next;
      },
      [draftRef],
    );
    const setThinkingElapsed = useCallback(() => {}, []);
    return useChatMessages({
      agentId,
      streamDraft: draftRef.current,
      streamDraftRef: draftRef as any,
      updateStreamDraft,
      isStreaming,
      setIsStreaming,
      setThinkingElapsed,
      activeAgentIdRef,
    });
  }

  beforeEach(() => {
    useAppStore.getState().clearChatSessions();
    useAppStore.getState().setProcessingAgents([]);
    vi.mocked(getChatMessages).mockReset();
    vi.mocked(getChatMessages).mockResolvedValue([]);
  });

  afterEach(() => {
    useAppStore.getState().setProcessingAgents([]);
    useAppStore.getState().clearChatSessions();
  });

  it("reloads from DB and clears the dead draft after a reconnect", async () => {
    const draftRef: { current: StreamDraft | null } = { current: null };
    const nullWrites = vi.fn();
    vi.mocked(getChatMessages).mockResolvedValue([
      {
        id: "assist-1",
        role: "assistant",
        content: "完整整轮文本",
        created_at: 1,
        is_streaming: 0,
        metadata: JSON.stringify({
          segments: [
            { type: "text", content: "第一段。" },
            { type: "tool_call", tool: "list_files", id: "t1", status: "ok" },
            { type: "text", content: "第二段。" },
          ],
        }),
      },
    ]);

    renderHook(() => useReconcileHarness("A", draftRef, nullWrites));
    await act(async () => {
      await Promise.resolve();
    });
    // 模拟断连时刻的在飞 draft（真实链路由 message_id/text_delta 建立；
    // 挂载 effect 本身会做一次初始化清理，先丢弃）
    draftRef.current = { assistantId: "assist-1", segments: [{ type: "text", content: "残留半句" }] };
    nullWrites.mockClear();
    const callsAfterMount = vi.mocked(getChatMessages).mock.calls.length;

    // socketReconnectVersion 只由真实重连发出（App onOpen 钩子，首连不
    // bump），生产里 version 0 → 1 就是第一次重连；挂载晚于重连由
    // reconnectSeenRef 挡住，无需额外哨兵。
    await act(async () => {
      useAppStore.getState().bumpSocketReconnect();
      await Promise.resolve();
    });
    expect(vi.mocked(getChatMessages).mock.calls.length).toBeGreaterThan(callsAfterMount);
    // agent 不在处理中 → 死 draft 清空，DB 权威行接管
    expect(nullWrites).toHaveBeenCalled();
    expect(draftRef.current).toBeNull();
  });

  it("keeps the live draft when the agent is still processing after reconnect", async () => {
    const draftRef: { current: StreamDraft | null } = { current: null };
    const nullWrites = vi.fn();
    vi.mocked(getChatMessages).mockResolvedValue([]);
    useAppStore.getState().setProcessingAgents(["A"]);

    renderHook(() => useReconcileHarness("A", draftRef, nullWrites));
    await act(async () => {
      await Promise.resolve();
    });
    draftRef.current = { assistantId: "assist-1", segments: [{ type: "text", content: "在飞" }] };
    nullWrites.mockClear();

    await act(async () => {
      useAppStore.getState().bumpSocketReconnect();
      await Promise.resolve();
    });
    await act(async () => {
      useAppStore.getState().bumpSocketReconnect();
      await Promise.resolve();
    });
    expect(nullWrites).not.toHaveBeenCalled();
    expect(draftRef.current).not.toBeNull();
  });
});

// idle 可能早于 done 抵达（两者走不同频道）。done 是「按 metadata.segments
// 权威重载」的唯一入口，被丢弃就会让气泡永久停在流式中途的 DB 快照上
// （只剩最后一轮 content，think/旁白/工具块全消失）。
describe("useChatMessages — idle-before-done grace window", () => {
  const draft: StreamDraft = {
    assistantId: "assist-1",
    segments: [{ type: "text", content: "hi" }],
  };

  function useGraceHarness(agentId: string, draftRef: { current: StreamDraft | null }) {
    const activeAgentIdRef = useRef<string | null>(agentId);
    activeAgentIdRef.current = agentId;
    const [isStreaming, setIsStreaming] = useState(false);
    const updateStreamDraft = useCallback(
      (u: any) => {
        const next = typeof u === "function" ? u(draftRef.current) : u;
        // 真实链路里 draft 由 message_id / text_delta 事件建立，挂载期的
        // hydrate 不会把一个正在飞的 draft 抹成 null。忽略这类 null 写入，
        // 否则 draft 在断言前就被清掉，测不到宽限分支。
        if (next === null) return;
        draftRef.current = next;
      },
      [draftRef],
    );
    const setThinkingElapsed = useCallback(() => {}, []);
    return useChatMessages({
      agentId,
      streamDraft: draftRef.current,
      streamDraftRef: draftRef as any,
      updateStreamDraft,
      isStreaming,
      setIsStreaming,
      setThinkingElapsed,
      activeAgentIdRef,
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.getState().clearChatSessions();
    useAppStore.getState().setProcessingAgents([]);
    vi.mocked(getChatMessages).mockReset();
    vi.mocked(getChatMessages).mockResolvedValue([]);
    vi.mocked(subscribeAgentStream).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    useAppStore.getState().setProcessingAgents([]);
    useAppStore.getState().clearChatSessions();
  });

  it("keeps the passive subscription alive when idle lands before done", async () => {
    const draftRef = { current: draft as StreamDraft | null };
    const unsub = vi.fn();
    vi.mocked(subscribeAgentStream).mockReturnValue(unsub);
    useAppStore.getState().setProcessingAgents(["A"]);

    renderHook(() => useGraceHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    expect(subscribeAgentStream).toHaveBeenCalled();
    const callsBefore = unsub.mock.calls.length;

    // idle 先到：draft 仍在飞 → 不得摘掉 handler，否则 done 无人接收
    await act(async () => {
      useAppStore.getState().setProcessingAgents([]);
      await Promise.resolve();
    });
    expect(unsub.mock.calls.length).toBe(callsBefore);
  });

  it("force-closes the round once the grace window expires (done never arrived)", async () => {
    const draftRef = { current: draft as StreamDraft | null };
    const unsub = vi.fn();
    vi.mocked(subscribeAgentStream).mockReturnValue(unsub);
    useAppStore.getState().setProcessingAgents(["A"]);

    renderHook(() => useGraceHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      useAppStore.getState().setProcessingAgents([]);
      await Promise.resolve();
    });
    const callsBefore = unsub.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(9000);
      await Promise.resolve();
    });
    expect(unsub.mock.calls.length).toBeGreaterThan(callsBefore);
  });

  it("does not let a neighbour agent's churn extend the window indefinitely", async () => {
    const draftRef = { current: draft as StreamDraft | null };
    const unsub = vi.fn();
    vi.mocked(subscribeAgentStream).mockReturnValue(unsub);
    useAppStore.getState().setProcessingAgents(["A"]);

    renderHook(() => useGraceHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      useAppStore.getState().setProcessingAgents([]);
      await Promise.resolve();
    });
    const callsBefore = unsub.mock.calls.length;

    // 邻居 agent 反复翻转 processing → effect 重跑。deadline 存 ref，
    // 不得因此重置满窗口（否则强制收口永远不触发）。
    for (let i = 0; i < 6; i++) {
      await act(async () => {
        vi.advanceTimersByTime(1400);
        useAppStore.getState().updateProcessingAgent(`neighbour-${i}`, true);
        await Promise.resolve();
      });
    }
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(unsub.mock.calls.length).toBeGreaterThan(callsBefore);
  });
});

// TEST_DSH_44 Bug#1（被动路径）：一个 turn 有两次 done——streamer 收尾 done
// （core.py stream finally，早于 handle_completion 落库 metadata.segments）
// + completion 落库后的权威 done。第一次到达时 DB 快照无 segments，
// fetch-then-swap gate 必须拦住；重试窗口与耗尽后 draft 都按 persisted
// 结构化渲染（不再「超限放行」清 draft 退回平铺 content）。
describe("useChatMessages — done 收口 gate（TEST_DSH_44 Bug#1）", () => {
  const midStreamDbRow = {
    id: "assist-1",
    role: "assistant",
    content: "中途旁白拼接平文本",
    created_at: 1,
    is_streaming: 1,
  };
  const settledDbRow = {
    id: "assist-1",
    role: "assistant",
    content: "中途旁白拼接平文本",
    created_at: 1,
    is_streaming: 0,
    metadata: JSON.stringify({
      segments: [
        { type: "text", content: "第一轮旁白" },
        { type: "round_boundary", round: 1 },
        { type: "tool_call", tool: "list_files", id: "t1", status: "ok" },
        { type: "text", content: "第二轮旁白" },
      ],
    }),
  };

  function useGateHarness(
    agentId: string,
    draftRef: { current: StreamDraft | null },
  ) {
    const activeAgentIdRef = useRef<string | null>(agentId);
    activeAgentIdRef.current = agentId;
    const [isStreaming, setIsStreaming] = useState(false);
    const updateStreamDraft = useCallback((u: any) => {
      const next = typeof u === "function" ? u(draftRef.current) : u;
      draftRef.current = next;
    }, [draftRef]);
    const setThinkingElapsed = useCallback(() => {}, []);
    return useChatMessages({
      agentId,
      streamDraft: draftRef.current,
      streamDraftRef: draftRef as any,
      updateStreamDraft,
      isStreaming,
      setIsStreaming,
      setThinkingElapsed,
      activeAgentIdRef,
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.getState().clearChatSessions();
    useAppStore.getState().setProcessingAgents([]);
    vi.mocked(getChatMessages).mockReset();
    vi.mocked(getChatMessages).mockResolvedValue([]);
    vi.mocked(subscribeAgentStream).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    useAppStore.getState().setProcessingAgents([]);
    useAppStore.getState().clearChatSessions();
  });

  it("done#1 快照无 segments → draft 保留 persisted；被动订阅保留到 swap 终态（P1-1），done#2 重新进 gate", async () => {
    // ws.ts 的 _agentHandlers 是单槽 map：subscribe 注册、unsub 摘除，
    // 槽位为 null 时事件被丢弃。用它验证订阅保留/释放时机。
    let slot: ((event: any) => void) | null = null;
    vi.mocked(subscribeAgentStream).mockImplementation((_id: string, onEvent: any) => {
      slot = onEvent;
      return () => {
        if (slot === onEvent) slot = null;
      };
    });
    const fire = (event: any) => slot?.(event);
    vi.mocked(getChatMessages).mockResolvedValue([midStreamDbRow]);
    useAppStore.getState().setProcessingAgents(["A"]);

    const draftRef: { current: StreamDraft | null } = { current: null };
    renderHook(() => useGateHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    expect(slot).toBeTruthy();

    // message_id + text_delta 建立在飞 draft
    await act(async () => {
      fire({
        type: "message_id",
        data: JSON.stringify({ role: "assistant", id: "assist-1" }),
      });
    });
    await act(async () => {
      fire({ type: "text_delta", data: "第一轮旁白" });
    });
    expect(draftRef.current?.assistantId).toBe("assist-1");

    // done#1（streamer 收尾，segments 未落库）→ draft 切 persisted 留屏
    await act(async () => {
      fire({ type: "done", data: "" });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(draftRef.current).not.toBeNull();
    expect(draftRef.current?.persisted).toBe(true);
    // P1-1 核心：done#1 不摘被动订阅（swap 终态才摘）——权威 done#2 必须能进 gate
    expect(slot).not.toBeNull();

    // 两次重试仍无 segments（不清 draft）
    for (let i = 0; i < 2; i++) {
      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });
    }
    expect(draftRef.current?.persisted).toBe(true);
    expect(slot).not.toBeNull();

    // 权威 done#2（重试窗口内到达）：DB 快照已带 metadata.segments → swap
    vi.mocked(getChatMessages).mockResolvedValue([settledDbRow]);
    await act(async () => {
      fire({ type: "done", data: "" });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(draftRef.current).toBeNull();
    // swap 终态：被动订阅此时才释放
    expect(slot).toBeNull();
    // 面板渲染走 DB 权威行（结构化 segments），不是平铺 content
    // （store.ts 的本地 ChatMessage 接口没有 _segments 字段，断言用 chat 类型）
    const settled = useAppStore.getState().chatSessions.A?.find(
      (m) => m.id === "assist-1",
    ) as ChatMessage | undefined;
    expect(settled?._segments?.map((s) => s.type)).toEqual([
      "text",
      "round_boundary",
      "tool_call",
      "text",
    ]);
  });

  it("done#1 重试耗尽（快照始终无 segments）→ 不清 draft、按 persisted 结构化留屏", async () => {
    let slot: ((event: any) => void) | null = null;
    vi.mocked(subscribeAgentStream).mockImplementation((_id: string, onEvent: any) => {
      slot = onEvent;
      return () => {
        if (slot === onEvent) slot = null;
      };
    });
    vi.mocked(getChatMessages).mockResolvedValue([midStreamDbRow]);
    useAppStore.getState().setProcessingAgents(["A"]);

    const draftRef: { current: StreamDraft | null } = { current: null };
    renderHook(() => useGateHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      slot?.({
        type: "message_id",
        data: JSON.stringify({ role: "assistant", id: "assist-1" }),
      });
    });
    await act(async () => {
      slot?.({ type: "done", data: "" });
      await Promise.resolve();
      await Promise.resolve();
    });

    // 5×400ms 重试耗尽，快照始终无 segments → 不清 draft（旧实现此处坍缩）
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });
    }
    expect(draftRef.current).not.toBeNull();
    expect(draftRef.current?.persisted).toBe(true);
  });

  it("error 收口：draft 切 persisted 结构化渲染，不坍缩（P1-2）", async () => {
    let slot: ((event: any) => void) | null = null;
    vi.mocked(subscribeAgentStream).mockImplementation((_id: string, onEvent: any) => {
      slot = onEvent;
      return () => {
        if (slot === onEvent) slot = null;
      };
    });
    const fire = (event: any) => slot?.(event);
    vi.mocked(getChatMessages).mockResolvedValue([midStreamDbRow]);
    useAppStore.getState().setProcessingAgents(["A"]);

    const draftRef: { current: StreamDraft | null } = { current: null };
    renderHook(() => useGateHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      fire({
        type: "message_id",
        data: JSON.stringify({ role: "assistant", id: "assist-1" }),
      });
    });
    await act(async () => {
      fire({ type: "text_delta", data: "第一轮旁白" });
    });
    expect(draftRef.current?.persisted).toBeUndefined();

    await act(async () => {
      fire({ type: "error", data: "boom" });
      await Promise.resolve();
      await Promise.resolve();
    });
    // P1-2 核心：error 也切 persisted —— 未 persisted 的 draft 会被 merge
    // 早退忽略（isStreaming=false），整轮结构化视图坍缩回平铺快照
    expect(draftRef.current).not.toBeNull();
    expect(draftRef.current?.persisted).toBe(true);
    // 错误收口无 done#2 可等，订阅照旧摘除
    expect(slot).toBeNull();
  });

  it("force-settle 后 30s 一次性复查：segments 补落则 swap 换场（P2-2）", async () => {
    let slot: ((event: any) => void) | null = null;
    vi.mocked(subscribeAgentStream).mockImplementation((_id: string, onEvent: any) => {
      slot = onEvent;
      return () => {
        if (slot === onEvent) slot = null;
      };
    });
    vi.mocked(getChatMessages).mockResolvedValue([midStreamDbRow]);
    useAppStore.getState().setProcessingAgents(["A"]);

    const draftRef: { current: StreamDraft | null } = { current: null };
    renderHook(() => useGateHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      slot?.({
        type: "message_id",
        data: JSON.stringify({ role: "assistant", id: "assist-1" }),
      });
    });
    await act(async () => {
      slot?.({ type: "text_delta", data: "第一轮旁白" });
    });
    // idle 先于 done（done 永远不来）→ 8s 宽限到期强制收口
    await act(async () => {
      useAppStore.getState().setProcessingAgents([]);
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(9000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(draftRef.current).not.toBeNull();
    expect(draftRef.current?.persisted).toBe(true);

    // 30s 一次性复查：segments 已补落 → swap，DB 权威行接管
    vi.mocked(getChatMessages).mockResolvedValue([settledDbRow]);
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(draftRef.current).toBeNull();

    // 一次性：再过 30s 无排程、无副作用（draft 已空，复查守卫直接放弃）
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(draftRef.current).toBeNull();
  });

  it("done 时快照已带 segments → 单次 done 即 swap（正常路径回归）", async () => {
    let streamOnEvent: (event: any) => void = () => {};
    vi.mocked(subscribeAgentStream).mockImplementation((_id: string, onEvent: any) => {
      streamOnEvent = onEvent;
      return () => {};
    });
    vi.mocked(getChatMessages).mockResolvedValue([settledDbRow]);
    useAppStore.getState().setProcessingAgents(["A"]);

    const draftRef: { current: StreamDraft | null } = { current: null };
    renderHook(() => useGateHarness("A", draftRef));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      streamOnEvent({
        type: "message_id",
        data: JSON.stringify({ role: "assistant", id: "assist-1" }),
      });
    });
    await act(async () => {
      streamOnEvent({ type: "done", data: "" });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(draftRef.current).toBeNull();
  });
});
