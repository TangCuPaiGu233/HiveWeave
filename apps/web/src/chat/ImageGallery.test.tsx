import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ImageGallery, singleFit } from "./ImageGallery";
import type { GalleryImage } from "./messageUtils";

/**
 * P1 图片 gallery + lightbox（2026-09-06）：
 * - singleFit 是 DSH MessageImage.tsx:45-57 的原样移植，数值逐例断言。
 * - gallery：单图 singleFit 框、多图 tile、点击开 lightbox、←→ 切换、
 *   Esc / 点遮罩关闭、计数 1/N。
 */

function mkImg(i: number, over: Partial<GalleryImage> = {}): GalleryImage {
  return { src: `data:image/png;base64,IMG${i}`, ...over };
}

describe("singleFit（DSH MessageImage.tsx:45-57 移植）", () => {
  it("横图：长边压到 240，比例保持", () => {
    expect(singleFit({ width: 480, height: 240 })).toEqual({
      width: 240,
      height: 120,
      objectPosition: "center",
    });
  });

  it("竖图：比例 clamp 到 0.25 下限，裁切锚点贴顶（信息密集侧）", () => {
    // natural ≈ 0.133 < 0.25 → ratio 0.25 → box 60x240；不放大 → 缩回 40x160
    expect(singleFit({ width: 40, height: 300 })).toEqual({
      width: 40,
      height: 160,
      objectPosition: "center top",
    });
  });

  it("超宽图：比例 clamp 到 4 上限，锚点贴左", () => {
    // natural 10 > 4 → ratio 4 → box 240x60（scale = min(1, …) = 1）
    expect(singleFit({ width: 1000, height: 100 })).toEqual({
      width: 240,
      height: 60,
      objectPosition: "left center",
    });
  });

  it("小于自然尺寸不放大：小图保持原尺寸", () => {
    expect(singleFit({ width: 100, height: 50 })).toEqual({
      width: 100,
      height: 50,
      objectPosition: "center",
    });
  });
});

describe("ImageGallery 分组渲染", () => {
  it("单图：一个 gallery 容器内一张图，无 lightbox 初始态", () => {
    const { container } = render(<ImageGallery images={[mkImg(1)]} />);
    expect(container.querySelectorAll("[data-hw-gallery]")).toHaveLength(1);
    expect(container.querySelectorAll("[data-hw-gallery] img")).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("多图：同一容器内渲染全部图片（并组，不各自成组）", () => {
    const { container } = render(<ImageGallery images={[mkImg(1), mkImg(2), mkImg(3)]} />);
    const galleries = container.querySelectorAll("[data-hw-gallery]");
    expect(galleries).toHaveLength(1);
    expect(galleries[0].querySelectorAll("img")).toHaveLength(3);
  });

  it("空列表不渲染", () => {
    const { container } = render(<ImageGallery images={[]} />);
    expect(container.querySelector("[data-hw-gallery]")).toBeNull();
  });
});

describe("ImageLightbox 开关与切换", () => {
  it("点击第 2 张打开 lightbox，计数 2/3；→ 切到 3/3；边界停住不回绕", () => {
    const { container } = render(<ImageGallery images={[mkImg(1), mkImg(2), mkImg(3)]} />);
    const tiles = container.querySelectorAll<HTMLButtonElement>("[data-hw-gallery] button");
    fireEvent.click(tiles[1]);
    const dialog = screen.getByRole("dialog", { name: "图片预览 2 / 3" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    // 点击的是第 2 张：大图 src 对应第 2 个
    expect(dialog.querySelector("img")?.getAttribute("src")).toContain("IMG2");

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    // 已到末尾：再按 → 停住
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("3 / 3")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("Esc 关闭；重开后点遮罩也关闭", () => {
    const { container } = render(<ImageGallery images={[mkImg(1), mkImg(2)]} />);
    fireEvent.click(container.querySelectorAll("[data-hw-gallery] button")[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    // 重开（回到第一次点开的那张：1/2）→ 点遮罩关闭
    fireEvent.click(container.querySelectorAll("[data-hw-gallery] button")[0]);
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("dialog", { name: "图片预览 1 / 2" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("点大图本身不关闭（stopPropagation）", () => {
    const { container } = render(<ImageGallery images={[mkImg(1)]} />);
    fireEvent.click(container.querySelector("[data-hw-gallery] button")!);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(dialog.querySelector("img")!);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("单图无左右切换按钮", () => {
    const { container } = render(<ImageGallery images={[mkImg(1)]} />);
    fireEvent.click(container.querySelector("[data-hw-gallery] button")!);
    expect(screen.queryByRole("button", { name: "上一张" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下一张" })).toBeNull();
  });

  it("多图首张无「上一张」（边界按钮隐藏）", () => {
    const { container } = render(<ImageGallery images={[mkImg(1), mkImg(2)]} />);
    fireEvent.click(container.querySelectorAll("[data-hw-gallery] button")[0]);
    expect(screen.queryByRole("button", { name: "上一张" })).toBeNull();
    expect(screen.getByRole("button", { name: "下一张" })).toBeInTheDocument();
  });
});
