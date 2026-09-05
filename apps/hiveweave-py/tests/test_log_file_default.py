"""日志落盘默认值（批次0）：HIVEWEAVE_LOG_FILE 未设也必须落盘。

45 轮运维缺口：手动 ``uv run uvicorn`` 绕过 .bat 脚本时 HIVEWEAVE_LOG_FILE
不生效，整轮平台日志只写控制台——retry 精确计数与 drift 探针验收被挡。
显式 env 保持 TEST21 M10 fail-closed 语义；默认兜底路径 best-effort，
且 pytest 进程不落盘（测试噪音不得混进运维日志）。
"""

from __future__ import annotations

import structlog

from hiveweave import main as hw_main


def test_resolve_explicit_env(monkeypatch, tmp_path):
    target = tmp_path / "explicit.log"
    monkeypatch.setenv("HIVEWEAVE_LOG_FILE", str(target))
    path, explicit = hw_main._resolve_log_path()
    assert path == str(target)
    assert explicit is True


def test_resolve_default_when_unset(monkeypatch):
    monkeypatch.delenv("HIVEWEAVE_LOG_FILE", raising=False)
    monkeypatch.setattr(hw_main, "_under_pytest", lambda: False)
    path, explicit = hw_main._resolve_log_path()
    assert explicit is False
    # 默认位固定在 apps/hiveweave-py/logs/server.out.log（包根相对）
    assert path.replace("\\", "/").endswith(
        "apps/hiveweave-py/logs/server.out.log"
    )


def test_resolve_skipped_under_pytest(monkeypatch):
    monkeypatch.delenv("HIVEWEAVE_LOG_FILE", raising=False)
    # 真实 pytest 进程内守卫生效：不落盘（防测试噪音污染运维日志）
    monkeypatch.setattr(hw_main, "_under_pytest", lambda: True)
    path, explicit = hw_main._resolve_log_path()
    assert (path, explicit) == ("", False)


def test_vital_sign_explicit_still_fatal_when_unwritable(monkeypatch):
    monkeypatch.setenv("HIVEWEAVE_LOG_FILE", "Z:/nonexistent/dir/x.log")
    monkeypatch.setattr(hw_main, "_LOG_FILE_STREAM", None)
    try:
        hw_main._assert_log_vital_sign()
    except SystemExit:
        pass
    else:
        raise AssertionError("explicit env + unwritable file must sys.exit(1)")


def test_vital_sign_default_degrades_not_fatal(monkeypatch):
    monkeypatch.delenv("HIVEWEAVE_LOG_FILE", raising=False)
    monkeypatch.setattr(hw_main, "_LOG_FILE_STREAM", None)
    # stream 打不开（显式=False，含默认路径落空场景）→ 静默降级，不得拖垮启动
    hw_main._assert_log_vital_sign()


def test_vital_sign_write_failure_degrades_when_not_explicit(monkeypatch, tmp_path):
    class _BoomStream:
        def write(self, _s):
            raise OSError("disk full")

        def flush(self):
            raise OSError("disk full")

    monkeypatch.delenv("HIVEWEAVE_LOG_FILE", raising=False)
    monkeypatch.setattr(hw_main, "_LOG_FILE_STREAM", _BoomStream())
    # 打开成功但 vital-sign 写入失败（非显式）→ WARNING 降级，不 SystemExit
    hw_main._assert_log_vital_sign()


def test_vital_sign_default_writes_ok_line(monkeypatch, tmp_path):
    log_file = tmp_path / "server.out.log"
    stream = hw_main._FlushFile(log_file)
    monkeypatch.delenv("HIVEWEAVE_LOG_FILE", raising=False)
    monkeypatch.setattr(hw_main, "_LOG_FILE_STREAM", stream)
    hw_main._assert_log_vital_sign()
    stream.close()
    assert "log_vital_sign" in log_file.read_text(encoding="utf-8")


def test_configure_logging_defaults_to_file(monkeypatch, tmp_path):
    log_file = tmp_path / "server.out.log"
    monkeypatch.setattr(hw_main, "_default_log_file", lambda: log_file)
    monkeypatch.setattr(hw_main, "_under_pytest", lambda: False)
    monkeypatch.delenv("HIVEWEAVE_LOG_FILE", raising=False)

    hw_main._configure_logging()
    structlog.get_logger("batch0_probe").info(
        "batch0_default_path_probe", marker="b0probe"
    )
    content = log_file.read_text(encoding="utf-8")
    assert "batch0_default_path_probe" in content
    assert "b0probe" in content

    # 定向还原全局 structlog 状态（勿用 monkeypatch.undo()——会连坐
    # conftest autouse fixture 的 env 剥离）。pytest 守卫下恢复=控制台。
    monkeypatch.setattr(hw_main, "_under_pytest", lambda: True)
    for var in ("HIVEWEAVE_LOG_FILE", "HIVEWEAVE_LOG_JSON", "HIVEWEAVE_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    hw_main._configure_logging()
    assert hw_main._LOG_FILE_STREAM is None


def test_flush_file_appends_across_restarts(tmp_path):
    log_file = tmp_path / "server.out.log"
    first = hw_main._FlushFile(log_file)
    first.write("line-one\n")
    first.close()
    second = hw_main._FlushFile(log_file)
    second.write("line-two\n")
    second.close()
    content = log_file.read_text(encoding="utf-8")
    assert "line-one" in content and "line-two" in content
