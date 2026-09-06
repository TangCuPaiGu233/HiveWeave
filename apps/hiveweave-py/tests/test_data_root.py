"""数据根函数测试（spec §11 打包税 #1）。

覆盖：
- 脚本模式默认 = 现有约定 apps/hiveweave-py/data（向后兼容，路径逐字节不变）
- 冻结 EXE 模式 = EXE 同级 data/
- HIVEWEAVE_DATA_ROOT env 显式覆盖（两种模式都生效）
- get_meta_db_path 经数据根解析（脚本模式默认值不变）
- 助理工作区 / 球位置文件一律挂在数据根下
"""

from __future__ import annotations

import sys
from pathlib import Path

from hiveweave import config
from hiveweave.config import (
    Settings,
    get_assistant_workspace,
    get_ball_position_file,
    get_data_root,
    is_frozen,
)


def test_script_mode_default_is_legacy_data_dir(monkeypatch):
    """脚本模式默认沿用 apps/hiveweave-py/data —— 向后兼容硬保证。"""
    monkeypatch.delenv("HIVEWEAVE_DATA_ROOT", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    expected = Path(config.__file__).resolve().parents[2] / "data"
    assert get_data_root() == expected.resolve()
    assert is_frozen() is False


def test_frozen_mode_data_beside_exe(monkeypatch, tmp_path):
    """冻结 EXE：数据根 = EXE 同级 data/（打包税 #1 便携布局）。"""
    monkeypatch.delenv("HIVEWEAVE_DATA_ROOT", raising=False)
    fake_exe = tmp_path / "app" / "HiveWeave.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert get_data_root() == (tmp_path / "app" / "data").resolve()
    assert is_frozen() is True


def test_env_override_wins_in_both_modes(monkeypatch, tmp_path):
    """HIVEWEAVE_DATA_ROOT 显式覆盖优先于两种默认模式。"""
    root = tmp_path / "custom-root"
    monkeypatch.setenv("HIVEWEAVE_DATA_ROOT", str(root))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert get_data_root() == root.resolve()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "x.exe"))
    assert get_data_root() == root.resolve()


def test_meta_db_path_routes_through_data_root(monkeypatch, tmp_path):
    """Meta DB 默认路径经数据根解析（spec §11：Meta DB 一律经它）。"""
    root = tmp_path / "root"
    monkeypatch.setenv("HIVEWEAVE_DATA_ROOT", str(root))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    s = Settings(_env_file=None)
    assert s.get_meta_db_path() == str((root / "hiveweave.db").resolve())
    # 显式 meta_db_path 仍然最高优先（向后兼容）
    s2 = Settings(_env_file=None, meta_db_path=str(tmp_path / "explicit.db"))
    assert s2.get_meta_db_path() == str(tmp_path / "explicit.db")


def test_legacy_default_meta_db_path_unchanged(monkeypatch):
    """脚本模式无 env 时，Meta DB 默认值与旧口径逐字节一致。"""
    monkeypatch.delenv("HIVEWEAVE_DATA_ROOT", raising=False)
    monkeypatch.delenv("HIVEWEAVE_META_DB_PATH", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    s = Settings(_env_file=None)
    expected = str(
        (Path(config.__file__).resolve().parents[2] / "data" / "hiveweave.db")
    )
    assert s.get_meta_db_path() == expected


def test_assistant_workspace_and_ball_position_under_data_root(
    monkeypatch, tmp_path
):
    """助理工作区 / 球位置文件一律经数据根取路径（spec §7/§10/§11）。"""
    root = tmp_path / "root"
    monkeypatch.setenv("HIVEWEAVE_DATA_ROOT", str(root))
    assert get_assistant_workspace() == (root / "assistant").resolve()
    assert get_ball_position_file() == (root / "ball_position.json").resolve()
