"""批次 D：图片预算确定性 offload 测试。"""

from __future__ import annotations

from hiveweave.services.vision import offload_old_images

_PLACEHOLDER = "[image offloaded: exceeded request image budget]"


def _img(size: int) -> dict:
    return {"media_type": "image/png", "data": "A" * size}


def test_under_budget_no_change():
    msgs = [{"role": "user", "content": "hi", "images": [_img(100)]}]
    result = offload_old_images(msgs, budget_bytes=1000)
    assert result[0]["images"][0]["data"] == "A" * 100


def test_over_budget_oldest_offloaded():
    msgs = [
        {"role": "user", "content": "old", "images": [_img(800)]},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "new", "images": [_img(600)]},
    ]
    result = offload_old_images(msgs, budget_bytes=1000)
    # 最老图被 offload（data 替换为占位）
    old_imgs = result[0]["images"]
    assert any(
        isinstance(d, dict) and "offloaded" in (d.get("data") or "")
        for d in old_imgs
    )


def test_no_images_untouched():
    msgs = [{"role": "user", "content": "text only"}]
    result = offload_old_images(msgs, budget_bytes=100)
    assert result == msgs


def test_exact_budget_no_offload():
    msgs = [{"role": "user", "content": "x", "images": [_img(100)]}]
    result = offload_old_images(msgs, budget_bytes=100)
    assert result[0]["images"][0]["data"] == "A" * 100


def test_input_not_mutated():
    msgs = [{"role": "user", "content": "x", "images": [_img(900)]}]
    original_data = msgs[0]["images"][0]["data"]
    offload_old_images(msgs, budget_bytes=50)
    assert msgs[0]["images"][0]["data"] == original_data  # 原输入不被修改
