"""HiveWeave 桌面悬浮球启动器（spec §10 / D8 / D9，打包税 #2 #3）。

单进程结构（D9）：主线程 pywebview GUI 循环 + 子线程 uvicorn；
``--headless`` 只跑 uvicorn（服务器/CI 无 GUI 降级），pywebview 惰性
导入 —— 无 GUI 环境/未安装 pywebview 时 headless 与 selfcheck 不崩。

窗口参数（F4 承重事实）：on_top / frameless / focus=False / easy_drag=False
+ DRAG_REGION_SELECTOR（球体与面板头，data-drag-region）；窗口移动由
pywebview 拖拽机制承担，js_api 只做位置存取与球态↔展开态缩放。
透明窗口是 F4 自相矛盾点 → 默认不透明保底视觉，env
``HIVEWEAVE_BALL_TRANSPARENT=1`` 才试验性开启（遗留验证清单 #2）。

用法（cwd 建议为 apps/hiveweave-py，.env 灌入依赖 cwd）::

    .venv/Scripts/python.exe -u ../desktop/launcher.py           # 球
    .venv/Scripts/python.exe -u ../desktop/launcher.py --headless
    .venv/Scripts/python.exe -u ../desktop/launcher.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

# 球壳窗口几何（§10：球 ≈60px，展开 ≈380px；四周留阴影余量）
BALL_W, BALL_H = 76, 76
PANEL_W, PANEL_H = 380, 540
_STARTUP_TIMEOUT_S = 60.0


def _repo_root_from_launcher() -> Path | None:
    """launcher.py 位于 <repo>/apps/desktop/ → 仓库根（脚本模式）。"""
    here = Path(__file__).resolve()
    if here.parent.name == "desktop" and here.parent.parent.name == "apps":
        return here.parent.parent.parent
    return None


def _prepare_cwd() -> None:
    """把 cwd 切到 apps/hiveweave-py（.env 与默认数据目录约定依赖 cwd）。

    frozen 模式不切（数据一律走 EXE 同级 data/，见 config.get_data_root）。
    """
    if getattr(sys, "frozen", False):
        return
    repo = _repo_root_from_launcher()
    if repo is not None:
        backend = repo / "apps" / "hiveweave-py"
        if backend.is_dir():
            os.chdir(backend)
            src = backend / "src"
            if src.is_dir() and str(src) not in sys.path:
                sys.path.insert(0, str(src))


def _load_position() -> dict:
    """读球位置（数据根 ball_position.json —— spec §10 位置记忆）。"""
    try:
        from hiveweave.config import get_ball_position_file

        p = get_ball_position_file()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            x, y = data.get("x"), data.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return {"x": int(x), "y": int(y)}
    except Exception:
        pass
    return {}


def _save_position(x: int | None, y: int | None) -> None:
    try:
        if x is None or y is None:
            return
        from hiveweave.config import get_ball_position_file

        p = get_ball_position_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8"
        )
    except Exception:
        pass  # 位置记忆失败不影响运行


class BallApi:
    """pywebview js_api 桥 — 窗口移动/位置存取/球态缩放（§10）。

    pywebview 在后台线程调用这些方法；webview 对象经 ``_win`` 注入。
    """

    def __init__(self) -> None:
        self._win = None
        self._server = None

    def attach(self, win, server) -> None:
        self._win = win
        self._server = server

    # ── 内部 ────────────────────────────────────────────────
    def _cur_xy(self) -> tuple[int | None, int | None]:
        try:
            return getattr(self._win, "x", None), getattr(self._win, "y", None)
        except Exception:
            return None, None

    # ── JS 桥方法 ───────────────────────────────────────────
    def save_position(self) -> dict:
        x, y = self._cur_xy()
        _save_position(x, y)
        return {"ok": True, "x": x, "y": y}

    def expand(self) -> dict:
        """球态 → 展开态（保持左上角锚点，向右下扩）。"""
        try:
            if self._win is not None:
                self._win.resize(PANEL_W, PANEL_H)
        except Exception:
            pass
        return {"ok": True}

    def collapse(self) -> dict:
        try:
            if self._win is not None:
                self._win.resize(BALL_W, BALL_H)
                self.save_position()
        except Exception:
            pass
        return {"ok": True}

    def get_api_key(self) -> str:
        """审计 M3：HIVEWEAVE_API_KEY 启用时球页经此取 key（同进程内存，
        不落盘不入 URL）。无 key 部署返回空串，球页按原样匿名请求。"""
        try:
            from hiveweave.config import settings

            return str(getattr(settings, "api_key", "") or "")
        except Exception:
            return ""

    def open_main(self) -> dict:
        """打开主界面：优先 Vite dev :5173，未运行则回落 :4000（打包税 #2）。"""
        import webbrowser

        try:
            from hiveweave.config import settings

            port = settings.port
        except Exception:
            port = 4000
        url = os.environ.get("HIVEWEAVE_MAIN_URL") or ""
        if not url:
            import urllib.request

            dev = "http://127.0.0.1:5173"
            try:
                urllib.request.urlopen(dev, timeout=1)
                url = dev
            except Exception:
                url = f"http://127.0.0.1:{port}/"
        webbrowser.open(url)
        return {"ok": True, "url": url}

    def hide_ball(self) -> dict:
        """隐藏到托盘的 P0 替代：藏窗口（进程与后端保留）。"""
        try:
            if self._win is not None:
                self._win.hide()
        except Exception:
            pass
        return {"ok": True}

    def quit(self) -> dict:
        try:
            if self._server is not None:
                self._server.should_exit = True
            if self._win is not None:
                self._win.destroy()
        except Exception:
            pass
        return {"ok": True}


def _run_selfcheck() -> int:
    """无人环境静态验收：可导入、数据根可解析、前端构建产物自洽。"""
    failures: list[str] = []

    from hiveweave.config import (
        get_assistant_workspace,
        get_ball_position_file,
        get_data_root,
        get_meta_db_path,
        is_frozen,
    )

    print(f"frozen={is_frozen()}")
    print(f"data_root={get_data_root()}")
    print(f"meta_db={get_meta_db_path()}")
    print(f"assistant_workspace={get_assistant_workspace()}")
    print(f"ball_position_file={get_ball_position_file()}")

    from hiveweave.api.ball import resolve_ball_static_dir

    ball_dir = resolve_ball_static_dir()
    print(f"ball_static_dir={ball_dir}")
    if ball_dir is None:
        print("WARN: ball static dir not found (fallback page will serve)")
        failures.append("ball_static_dir_missing")

    import hiveweave.main as main_mod  # noqa: F401  (app 可构建)

    print("backend_app_import=ok")

    for name in (
        "services.assistant",
        "services.ball_bridge",
        "services.user_message",
        "api.ball",
    ):
        __import__(f"hiveweave.{name}")
        print(f"import {name}=ok")

    try:
        import webview  # noqa: F401

        print("pywebview=installed")
    except Exception:
        print("pywebview=NOT installed (GUI disabled; --headless still works)")

    if failures:
        print("SELFCHECK FAIL: " + ", ".join(failures))
        return 1
    print("SELFCHECK OK")
    return 0


def _wait_server(server, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return True
        if getattr(server, "should_exit", False):
            return False
        time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HiveWeave 桌面悬浮球启动器")
    parser.add_argument("--headless", action="store_true", help="只跑后端，不开球窗口")
    parser.add_argument("--url", default="", help="覆盖球页面 URL（默认 :<port>/ball）")
    parser.add_argument("--port", type=int, default=None, help="后端端口（默认 HIVEWEAVE_PORT/4000）")
    parser.add_argument("--selfcheck", action="store_true", help="无人环境静态自检后退出")
    args = parser.parse_args(argv)

    _prepare_cwd()

    if args.selfcheck:
        return _run_selfcheck()

    import uvicorn

    from hiveweave.config import settings

    port = args.port or settings.port
    host = settings.host or "127.0.0.1"

    config = uvicorn.Config(
        "hiveweave.main:app",
        host=host,
        port=port,
        workers=1,
        log_config=None,  # structlog 已配置，别让 uvicorn 再动 logging
        timeout_keep_alive=30,
    )

    if args.headless:
        # 打包税 #3：headless = 主线程跑 uvicorn（正常信号处理）
        uvicorn.run(config)
        return 0

    # GUI 模式：子线程 uvicorn，主线程 pywebview（D9：GUI 循环必须主线程）
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    server_thread.start()
    if not _wait_server(server, _STARTUP_TIMEOUT_S):
        print(
            f"FATAL: backend did not start on {host}:{port} within "
            f"{int(_STARTUP_TIMEOUT_S)}s",
            file=sys.stderr,
        )
        return 1

    ball_url = (
        args.url
        or os.environ.get("HIVEWEAVE_BALL_URL", "").strip()
        or f"http://127.0.0.1:{port}/ball"
    )

    try:
        import webview
    except Exception as e:
        print(
            "FATAL: pywebview 未安装（GUI 模式需要；headless 不需要）。"
            f"安装：uv sync --extra desktop（或 pip install pywebview）。原因：{e}",
            file=sys.stderr,
        )
        server.should_exit = True
        return 1

    # DRAG_REGION_SELECTOR（§10）：球体与面板头都标 data-drag-region
    webview.DRAG_REGION_SELECTOR = "[data-drag-region]"

    pos = _load_position()
    api = BallApi()
    transparent = os.environ.get("HIVEWEAVE_BALL_TRANSPARENT", "").lower() in (
        "1", "true", "yes"
    )
    win = webview.create_window(
        "HiveWeave",
        ball_url,
        js_api=api,
        width=BALL_W,
        height=BALL_H,
        x=pos.get("x"),
        y=pos.get("y"),
        frameless=True,      # 无边框（F4）
        on_top=True,         # Windows TopMost（F4）
        focus=False,         # 不抢焦点（F4）
        easy_drag=False,     # 拖动只认 DRAG_REGION_SELECTOR（§10）
        transparent=transparent,  # 默认 False：不透明保底视觉（F4 矛盾点）
    )
    api.attach(win, server)
    webview.start()
    # GUI 退出 → 停后端，进程随 daemon 线程结束
    server.should_exit = True
    server_thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
