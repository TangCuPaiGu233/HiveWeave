import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage, MsgSegment } from "./types";

/**
 * 轮次分隔渲染统一（2026-09-05）：round_boundary 段由 live draft
 * （beginStreamRound）与持久化消息（后端 build_display_segments）产同
 * kind 段，MessageBubble 同一分支渲染 —— 这里断言两种来源渲染一致，
 * 且旧「—— 第 N 轮 ——」伪 text 标记不再以文本形式出现。
 */

function mkMsg(segments: MsgSegment[], over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "",
    timestamp: 1,
    _segments: segments,
    ...over,
  };
}

describe("MessageBubble round_boundary 渲染（live==persisted）", () => {
  it("持久化 segments：round_boundary 渲染为带轮号标签的分隔线", () => {
    render(
      <MessageBubble
        msg={mkMsg([
          { type: "text", content: "第一轮旁白" },
          { type: "round_boundary", round: 1 },
          { type: "text", content: "第二轮旁白" },
        ])}
      />,
    );
    expect(screen.getByText("第一轮旁白")).toBeInTheDocument();
    expect(screen.getByText("第二轮旁白")).toBeInTheDocument();
    // round 0 起号 → 显示 N+1；separator role + aria-label 可达性
    const divider = screen.getByRole("separator", { name: "第 2 轮" });
    expect(divider).toBeInTheDocument();
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
  });

  it("live draft 与持久化同分支：同 kind 段渲染出同样的轮次分隔", () => {
    // live draft 的 _segments 由 mergeStreamDraftIntoMessages 原样携带，
    // 渲染路径与持久化消息完全一致 —— 用相同 segments 断言分隔线输出一致。
    const segments: MsgSegment[] = [
      { type: "text", content: "旁白A" },
      { type: "round_boundary", round: 2 },
      { type: "text", content: "旁白B" },
    ];
    const first = render(<MessageBubble msg={mkMsg(segments)} />);
    const persistedDivider = first.container.querySelector('[role="separator"]')?.outerHTML;
    first.unmount();
    const second = render(<MessageBubble msg={mkMsg(segments, { isStreaming: true })} />);
    const liveDivider = second.container.querySelector('[role="separator"]')?.outerHTML;
    expect(persistedDivider).toBeTruthy();
    // streaming 气泡额外有光标等装饰，但分隔线本身逐字节一致
    expect(liveDivider).toBe(persistedDivider);
    expect(second.getByText("第 3 轮")).toBeInTheDocument();
  });

  it("缺轮号的 round_boundary 渲染兜底文案，不抛错", () => {
    render(
      <MessageBubble
        msg={mkMsg([
          { type: "text", content: "旁白" },
          { type: "round_boundary" },
        ])}
      />,
    );
    expect(screen.getByRole("separator", { name: "新一轮" })).toBeInTheDocument();
  });
});

/**
 * P0 富文本（2026-09-05）：text 段经 MarkdownText 做 markdown 渲染。
 * 安全基线见 MarkdownText.tsx 头注释 / docs/2026-09-05/chat-rich-text-design.md §3.2：
 * 无 HTML 直通（不装 rehype-raw）、链接/图片协议白名单、外链 noopener。
 */
describe("MessageBubble text 段 markdown 渲染（P0）", () => {
  it("围栏代码块渲染为 pre>code 等宽块，内容逐字保留并带语言标记", () => {
    const { container } = render(
      <MessageBubble
        msg={mkMsg([{ type: "text", content: "看这段：\n```js\nconst a = 1;\nconsole.log(a);\n```" }])}
      />,
    );
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain("const a = 1;");
    expect(pre!.textContent).toContain("console.log(a);");
    const code = pre!.querySelector("code");
    expect(code?.className).toContain("language-js");
  });

  it("行内代码渲染为独立 <code>，不进 pre", () => {
    render(<MessageBubble msg={mkMsg([{ type: "text", content: "运行 `npm test` 验证" }])} />);
    const inline = screen.getByText("npm test");
    expect(inline.tagName).toBe("CODE");
    expect(inline.closest("pre")).toBeNull();
  });

  it("http 外链带 target=_blank 与 rel=noopener noreferrer", () => {
    render(
      <MessageBubble
        msg={mkMsg([{ type: "text", content: "参考 [文档](https://example.com/docs) 说明" }])}
      />,
    );
    const anchor = screen.getByText("文档") as HTMLAnchorElement;
    expect(anchor.href).toBe("https://example.com/docs");
    expect(anchor.getAttribute("target")).toBe("_blank");
    expect(anchor.getAttribute("rel")).toContain("noopener");
    expect(anchor.getAttribute("rel")).toContain("noreferrer");
  });

  it("javascript: 链接被协议白名单拒绝：不产出 <a>，文本保留", () => {
    const { container } = render(
      <MessageBubble msg={mkMsg([{ type: "text", content: "[点我](javascript:alert(1)) 领奖" }])} />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("点我")).toBeInTheDocument();
  });

  it("HTML 注入被转义：<img src=x onerror> 不执行、DOM 不出现 img 元素", () => {
    const { container } = render(
      <MessageBubble
        msg={mkMsg([
          { type: "text", content: '看看 <img src=x onerror="window.__hw_xss=1"> 这个' },
        ])}
      />,
    );
    expect((window as unknown as Record<string, unknown>).__hw_xss).toBeUndefined();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/看看/)).toBeInTheDocument();
  });

  it("流式中途未闭合围栏不崩：按 CommonMark 渐进渲染为代码块", () => {
    const { container } = render(
      <MessageBubble
        msg={mkMsg([{ type: "text", content: "代码如下：\n```python\nprint('hi'" }], {
          isStreaming: true,
        })}
      />,
    );
    const code = container.querySelector("pre code");
    expect(code).not.toBeNull();
    expect(code!.textContent).toContain("print('hi'");
  });
});

/**
 * P1 审计收尾（2026-09-05）：P1-1 user 消息 markdown 门控锁定、
 * P1-2 超长 text 段纯文本降级、safeUrlTransform 归一化加固三例。
 */
describe("MessageBubble text 段 P1 审计收尾", () => {
  it("P1-1 锁定：user 消息带 text segment 时仍纯文本渲染（markdown 不生效）", () => {
    const { container } = render(
      <MessageBubble
        msg={mkMsg([{ type: "text", content: "用户原文 **不加粗** `不代码`" }], { role: "user" })}
      />,
    );
    // markdown 语义元素一个都不允许出现
    expect(container.querySelector("strong, code, pre, h1")).toBeNull();
    // 字面文本原样保留（** 与反引号可见）
    expect(screen.getByText(/用户原文 \*\*不加粗\*\* `不代码`/)).toBeInTheDocument();
  });

  it("加固：data: URI 链接被协议白名单拒绝（不产出 <a>）", () => {
    const { container } = render(
      <MessageBubble
        msg={mkMsg([
          {
            type: "text",
            content:
              "[点我](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==) 领奖",
          },
        ])}
      />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("点我")).toBeInTheDocument();
  });

  it("加固：jav&#9;ascript: 经 WHATWG 归一化（剥 TAB）后协议为 javascript: 被拒", () => {
    // &#9; 在 CommonMark link destination 中解码为真实 TAB；new URL() 会剥
    // TAB 得到 javascript: 协议 —— 白名单按归一化结果判定必须拒。
    const { container } = render(
      <MessageBubble
        msg={mkMsg([{ type: "text", content: "[点我](<jav&#9;ascript:alert(1)>) 领奖" }])}
      />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("点我")).toBeInTheDocument();
  });

  it("加固：//evil.com 协议相对地址被拒（无 base 解析失败 → 不产出 <a>）", () => {
    const { container } = render(
      <MessageBubble
        msg={mkMsg([{ type: "text", content: "[点我](//evil.com/path) 领奖" }])}
      />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("点我")).toBeInTheDocument();
  });

  it("P1-2：超过 64KB 阈值的 text 段降级纯文本，markdown 管线不跑", () => {
    const big = "# 大标题 **加粗**\n\n" + "x".repeat(70 * 1024);
    const { container } = render(<MessageBubble msg={mkMsg([{ type: "text", content: big }])} />);
    // markdown 会渲染出 h1/strong —— 纯文本降级后全部保持字面
    expect(container.querySelector("h1, strong, pre, code")).toBeNull();
    expect(container.textContent).toContain("# 大标题 **加粗**");
  });
});
