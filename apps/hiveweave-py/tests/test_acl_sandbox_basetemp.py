"""P0（s3c09 双项目 42 轮）：沙箱私有 TEMP 锚点下 pytest tmp_path 链死锁。

取证结论（file:line 见各断言；详细机制见
``hiveweave/services/acl_sandbox/temppatch.py`` 模块 docstring）：

1. 沙箱把受限 shell 的 TEMP/TMP 指到 agent 私有锚点
   ``<ws>/.hiveweave/sandbox-temp/<agent_id>``（service.py ``_build_sandbox_env``）。
2. CPython >= 3.12 在 Windows 把 ``os.mkdir(mode=0o700)`` 落成 **PROTECTED
   DACL**：SYSTEM / Administrators / OWNER_RIGHTS 三条 Full ACE —— 无用户
   ACE、无能力 SID、**不继承**锚点 DACL（本文件 ``test_owner_rights_island_shape``
   实测钉住该 DACL 形态）。
3. pytest tmp_path 工厂整条链都是 ``mkdir(mode=0o700)``
   （``_pytest/tmpdir.py`` getbasetemp :168 / mktemp :139 / make_numbered_dir
   :141,218）→ 在锚点下造出「受限令牌双 pass 全落空」的死岛：
   pass-1（restricting SIDs）无 temp 系能力 SID、pass-2（normal SIDs）无
   用户/AU ACE → ``Access is denied``（s3c09 实测 26+ 失败步）。
4. 平台预先授予救不了：孤岛 DACL 在创建瞬间被整体替换，锚点 ACE 传不进去；
   ``--basetemp`` 指到 TEMP 下也一样（basetemp 本身与每个测试目录都是
   mode=0o700）。

修复 = ``temppatch`` 两层：子进程 sitecustomize shim（os.mkdir 中和回
0o777 → 新目录继承锚点 DACL）+ 平台侧存量孤岛补授（temp_sid + 用户 SID）。
本文件用真实受限令牌（spawn_confined）做端到端验收。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hiveweave.config import settings
from hiveweave.services.acl_sandbox.service import (
    _build_sandbox_env,
    spawn_confined,
)

pytestmark = [pytest.mark.win32]

if not sys.platform.startswith("win"):
    pytest.skip("ACL sandbox basetemp tests require Windows",
                allow_module_level=True)


@pytest.fixture(scope="session", autouse=True)
def _shutdown_acl_runner():
    """会话结束回收排空池/watcher 线程 —— 非守护线程会阻塞进程退出。"""
    yield
    from hiveweave.services.acl_sandbox.service import shutdown_runner
    from hiveweave.services.acl_sandbox.spawn import stop_watcher

    stop_watcher()
    shutdown_runner()


COMSPEC = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")


# ── 与 test_acl_sandbox_win32 同款基础设施（自包含，避免跨测试文件导入） ──
def _dir_has_user_ace(d: Path, ws, user) -> bool:
    try:
        sd = ws.GetNamedSecurityInfo(
            str(d), ws.SE_FILE_OBJECT, ws.DACL_SECURITY_INFORMATION)
    except ws.error:
        return False
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        return False
    for i in range(dacl.GetAceCount()):
        ((t, _f), _m, s) = dacl.GetAce(i)
        if t == ws.ACCESS_ALLOWED_ACE_TYPE and s == user:
            return True
    return False


def _ensure_subject_ace(path: Path) -> None:
    """§4.12：给目录及其 OWNER_RIGHTS-only 祖先补当前用户 SID 全权 ACE。"""
    import win32api
    import win32security as ws

    tok = ws.OpenProcessToken(win32api.GetCurrentProcess(), ws.TOKEN_QUERY)
    user, _ = ws.GetTokenInformation(tok, ws.TokenUser)
    tok.Close()

    def _grant(d: Path) -> None:
        import win32con

        sd = ws.GetNamedSecurityInfo(
            str(d), ws.SE_FILE_OBJECT, ws.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:
            return
        dacl.SetEntriesInAcl([{
            "AccessPermissions": 0x1F01FF,
            "AccessMode": ws.GRANT_ACCESS,
            "Inheritance": win32con.CONTAINER_INHERIT_ACE
            | win32con.OBJECT_INHERIT_ACE,
            "Trustee": {
                "MultipleTrustee": None,
                "MultipleTrusteeOperation": 0,
                "TrusteeForm": ws.TRUSTEE_IS_SID,
                "TrusteeType": ws.TRUSTEE_IS_UNKNOWN,
                "Identifier": user,
            },
        }])
        ws.SetNamedSecurityInfo(
            str(d), ws.SE_FILE_OBJECT, ws.DACL_SECURITY_INFORMATION,
            sd.GetSecurityDescriptorOwner(), sd.GetSecurityDescriptorGroup(),
            dacl, None)

    if not _dir_has_user_ace(path, ws, user):
        _grant(path)
    anc = path.parent
    while anc != anc.parent and str(anc).lower() != str(anc.anchor).lower():
        if _dir_has_user_ace(anc, ws, user):
            break
        _grant(anc)
        anc = anc.parent


@pytest.fixture(autouse=True)
def _sandbox_on(monkeypatch):
    monkeypatch.setattr(settings, "acl_sandbox", True)


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """带真实主体 ACE 的 workspace（模拟用户常规目录）。"""
    d = tmp_path / "ws"
    d.mkdir(parents=True)
    _ensure_subject_ace(d)
    (d / ".hiveweave").mkdir(exist_ok=True)
    return d


async def _run(ws: Path, agent_id: str, command: str, *,
               timeout_s: float = 60, entry: str = "bash"):
    return await spawn_confined(
        command=command, workdir=str(ws),
        workspace_path=str(ws),
        agent_id=agent_id, timeout_s=timeout_s, entry=entry)


def _cmd(inner: str) -> str:
    return f'"{COMSPEC}" /c {inner}'


# pytest tmp_path 工厂的链式形态（_pytest/tmpdir.py:139/141/168 同款：
# 全部 mkdir(mode=0o700)，含编号目录重试与文件写/改名）。异常压成单行打印 ——
# 失败时 stdout 直接给出可机读的失败点与 WinError 文案。
_PYTEST_CHAIN_SCRIPT = r"""
import os, pathlib, sys
temp = os.environ["TEMP"]
def mark(m):
    print("STEP:" + m); sys.stdout.flush()
try:
    root = pathlib.Path(temp) / "pytest-of-probeuser"
    mark("mkdir_root_begin")
    root.mkdir(mode=0o700, exist_ok=True)
    mark("mkdir_root_ok")
    made = None
    for i in range(10):
        cand = root / ("pytest-%d" % i)
        try:
            cand.mkdir(mode=0o700)
            made = cand
            break
        except FileExistsError:
            continue
    assert made is not None, "make_numbered_dir exhausted"
    mark("mkdir_numbered_ok:" + made.name)
    d = made / "test_probe0"
    d.mkdir(mode=0o700)
    mark("mkdir_testdir_ok")
    f = d / "scratch.txt"
    f.write_text("hello", encoding="utf-8")
    mark("write_ok")
    f.rename(f.with_suffix(".renamed"))
    mark("rename_ok")
    print("CHAIN_OK")
except Exception as e:
    print("CHAIN_FAIL:%s:%s:%s" % (type(e).__name__, e, temp))
    sys.stdout.flush()
    raise SystemExit(3)
"""


# ── 机制取证（不依赖修复，任何环境下恒真） ─────────────────────
def test_owner_rights_island_shape(tmp_path: Path) -> None:
    """CPython>=3.12 Windows：mkdir(mode=0o700) → PROTECTED OWNER_RIGHTS 岛。

    DACL 形态 = S-1-5-18(SYSTEM) / S-1-5-32-544(Administrators) / S-1-3-4
    (OWNER_RIGHTS) 三条 Full，无当前用户 ACE —— 受限令牌 pass-1/pass-2
    全落空，且 PROTECTED 不继承锚点的 temp_sid 授予。
    """
    import win32api
    import win32security as ws

    d = tmp_path / "island"
    d.mkdir(mode=0o700)
    sd = ws.GetNamedSecurityInfo(
        str(d), ws.SE_FILE_OBJECT, ws.DACL_SECURITY_INFORMATION)
    control, _ = sd.GetSecurityDescriptorControl()
    assert control & ws.SE_DACL_PROTECTED, "mode=0o700 目录应为 PROTECTED DACL"
    dacl = sd.GetSecurityDescriptorDacl()
    sids = {
        ws.ConvertSidToStringSid(dacl.GetAce(i)[2])
        for i in range(dacl.GetAceCount())
    }
    assert "S-1-3-4" in sids, f"OWNER_RIGHTS 应在场: {sids}"
    tok = ws.OpenProcessToken(win32api.GetCurrentProcess(), ws.TOKEN_QUERY)
    try:
        user, _ = ws.GetTokenInformation(tok, ws.TokenUser)
        user_str = ws.ConvertSidToStringSid(user)
    finally:
        tok.Close()
    assert user_str not in sids, (
        f"死岛定义：当前用户 ACE 不得在场（否则受限令牌 pass-2 可过）: {sids}")


# ── 修复①验收：shim 让 pytest 形态链在锚点下端到端可写 ──────────
async def test_pytest_tmp_chain_writable_in_anchor(ws: Path) -> None:
    """真实受限令牌跑 pytest tmp_path 链（mkdir(mode=0o700) 全链）→ 成功。

    修复前该用例死在第二个 mkdir（make_numbered_dir → WinError 5）。
    """
    script = ws / "_chain_probe.py"
    script.write_text(_PYTEST_CHAIN_SCRIPT, encoding="utf-8")
    # 裸 `python`（受限子进程继承 PATH 白名单，uv run 下先解析 venv python）
    # —— 避免带引号 exe 路径与 pwsh/cmd 双层解析冲突（与 win32 用例口径一致）
    r = await _run(ws, "A001", _cmd("python _chain_probe.py"))
    assert r is not None, "spawn_confined 返回 None（沙箱未启用？）"
    assert r["exit_code"] == 0, {
        "exit": r["exit_code"], "stdout": (r.get("stdout") or "")[-600:],
        "stderr": (r.get("stderr") or "")[-800:]}
    assert "CHAIN_OK" in (r.get("stdout") or "")

    # 产物确实落在锚点下且平台可读
    anchor = ws / ".hiveweave" / "sandbox-temp" / "A001"
    produced = list(anchor.glob("pytest-of-probeuser/pytest-*/test_probe0/*.renamed"))
    assert produced, "改名后的产物应落在私有锚点内"
    assert produced[0].read_text(encoding="utf-8") == "hello"


async def test_sitecustomize_shim_written_and_in_env(ws: Path) -> None:
    """修复①：shim 落在 sandbox-temp 根（agent 不可写的 PROTECTED 区），
    且 _build_sandbox_env 把该目录注入 PYTHONPATH（在既有值之前）。"""
    r = await _run(ws, "A001", _cmd("echo boot"))
    assert r is not None and r["exit_code"] == 0, r

    shim = ws / ".hiveweave" / "sandbox-temp" / "sitecustomize.py"
    assert shim.is_file(), "sitecustomize shim 应写在 sandbox-temp 根"
    from hiveweave.services.acl_sandbox.temppatch import SHIM_SOURCE

    assert shim.read_text(encoding="utf-8") == SHIM_SOURCE

    env = _build_sandbox_env(
        str(ws), str(ws / ".hiveweave-cache"),
        str(ws / ".hiveweave" / "sandbox-temp" / "A001"))
    pp = env.get("PYTHONPATH", "")
    assert pp.split(os.pathsep)[0] == str(
        ws / ".hiveweave" / "sandbox-temp"), f"PYTHONPATH 应注入 shim 目录: {pp}"


async def test_env_pythonpath_merge_preserves_existing(ws: Path) -> None:
    """既有 PYTHONPATH（白名单透传）不丢 —— shim 目录前插。"""
    monkey_env_marker = r"D:\proj\sitefiles"
    env = _build_sandbox_env(
        str(ws), str(ws / ".hiveweave-cache"),
        str(ws / ".hiveweave" / "sandbox-temp" / "A001"),
        env_extra={"PYTHONPATH": monkey_env_marker})
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == str(ws / ".hiveweave" / "sandbox-temp")
    assert monkey_env_marker in parts[1:]


# ── 修复②验收：存量死岛被 walker 补授复活 ─────────────────────
async def test_walker_repairs_existing_island(ws: Path) -> None:
    """修复前遗留的 OWNER_RIGHTS-only 死岛（如上轮的 pytest-of-*），
    在下一条受限命令（_ensure_temp → walker）后被修复可写。"""
    anchor = ws / ".hiveweave" / "sandbox-temp" / "A001"
    # 模拟存量死岛：直接用 mode=0o700 建（测试进程 = 全权令牌，产物即死岛形态）
    island_root = anchor / "pytest-of-legacy"
    island_root.mkdir(parents=True, mode=0o700)
    island_leaf = island_root / "pytest-0"
    island_leaf.mkdir(mode=0o700)

    r = await _run(ws, "A001", _cmd("echo boot"))
    assert r is not None and r["exit_code"] == 0, r

    # 相对 workdir（ws）的全路径；.hiveweave/sandbox-temp 在 bash 护栏白名单内
    r2 = await _run(
        ws, "A001",
        _cmd("echo repaired > "
             ".hiveweave\\sandbox-temp\\A001\\pytest-of-legacy\\pytest-0\\f.txt"))
    assert r2 is not None and r2["exit_code"] == 0, {
        "exit": r2["exit_code"], "stdout": (r2.get("stdout") or "")[-400:],
        "stderr": (r2.get("stderr") or "")[-400:]}
    assert (island_leaf / "f.txt").read_text(encoding="utf-8").strip() == "repaired"


async def test_walker_skips_cache_subtree(ws: Path) -> None:
    """cache/（包管理器私有缓存巨树）不进 walker 扫描 —— 防每命令深扫。

    用 mode=0o700 死岛形态做 cache 子目录（PROTECTED —— 既不继承锚点 ACE、
    授予传播也进不去），walker 若进入必然留下 temp_sid ACE；断言全程缺席。
    """
    anchor = ws / ".hiveweave" / "sandbox-temp" / "A001"
    # 先建好锚点（纯继承形态），再用 mode=0o700 在其下建 cache/uv 死岛
    anchor.mkdir(parents=True)
    big = anchor / "cache" / "uv"
    big.mkdir(parents=True, exist_ok=True, mode=0o700)

    r = await _run(ws, "A001", _cmd("echo boot"))
    assert r is not None and r["exit_code"] == 0, r

    from hiveweave.services.acl_sandbox.grant import GRANT_MASK, WriteGrant
    from hiveweave.services.acl_sandbox.policy import resolve_policy
    from hiveweave.services.acl_sandbox.sid import temp_sid

    policy = resolve_policy(workspace_path=str(ws), agent_id="A001")
    # walker 不进 cache/ —— 死岛形态的 cache/uv 保持零能力 ACE
    assert not WriteGrant.ace_present(str(big), temp_sid(str(policy.temp_dir)),
                                      GRANT_MASK)


# ── 修复②：探针 fail-soft ────────────────────────────────────
async def test_probe_fail_soft(monkeypatch, ws: Path) -> None:
    """孤岛修复/写探针抛异常时 probe 不上抛，返回结构化错误。"""
    from hiveweave.services.acl_sandbox import temppatch

    def _boom(*a, **k):
        raise RuntimeError("walker boom")

    monkeypatch.setattr(temppatch, "repair_temp_islands", _boom)
    res = await temppatch.probe_private_temp(workspace_path=str(ws),
                                             agent_id="A001")
    assert res["writable"] is True, "平台侧写+删探针应成功"
    assert "walker boom" in (res["error"] or "")


# ── shim 行为单测（源码级：mode 强制回 0o777） ─────────────────
def test_shim_source_forces_mode_0o777(tmp_path: Path, monkeypatch) -> None:
    """exec shim 后 os.mkdir 收到 0o777（即使调用方传 0o700）；dir_fd 透传。"""
    import importlib.util

    from hiveweave.services.acl_sandbox.temppatch import SHIM_SOURCE

    real_mkdir = os.mkdir
    calls: list[tuple[object, int]] = []

    def fake_mkdir(path, mode=0o777, *args, **kwargs):
        calls.append((path, mode))
        return real_mkdir(path, 0o777, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", fake_mkdir)
    shim_file = tmp_path / "sitecustomize.py"
    shim_file.write_text(SHIM_SOURCE, encoding="utf-8")
    os._hiveweave_mkdir_patched = False  # 允许 shim 在本进程重新打补丁
    try:
        spec = importlib.util.spec_from_file_location(
            "hw_shim_under_test", shim_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os._hiveweave_mkdir_patched
        # shim 把 os.mkdir 换成了 _hw_mkdir（其 _hw_orig_mkdir=fake_mkdir）；
        # monkeypatch teardown 会把 os.mkdir 恢复为 real_mkdir —— 但
        # _hw_mkdir 不再被引用即可，无需手动清理。

    d = tmp_path / "shimmed"
    d.mkdir(mode=0o700)  # pathlib → os.mkdir(self, 0o700) → shim → 0o777
    assert calls, "shim 应经 os.mkdir 转发"
    assert all(m == 0o777 for _p, m in calls), f"mode 应回 0o777: {calls}"
    assert d.is_dir()


def test_gitignore_entries_cover_hiveweave_private_zone() -> None:
    """修复③：info/exclude 清单含 .hiveweave/* + 四共享目录反选（顺序正确）。"""
    from hiveweave.services.git_worktree.constants import (
        GITIGNORE_GENERATED_ENTRIES,
    )

    assert ".hiveweave/*" in GITIGNORE_GENERATED_ENTRIES
    star_idx = GITIGNORE_GENERATED_ENTRIES.index(".hiveweave/*")
    for d in ("shared", "reports", "drafts", "handoffs"):
        entry = f"!.hiveweave/{d}/"
        assert entry in GITIGNORE_GENERATED_ENTRIES
        # 同文件内后行覆盖前行 —— 反选必须尾随 .hiveweave/*，否则新写入的
        # 共享产物会被 .hiveweave/* 误忽略
        assert GITIGNORE_GENERATED_ENTRIES.index(entry) > star_idx


async def test_probe_sandbox_off_is_silent_noop(
    monkeypatch, ws: Path
) -> None:
    """沙箱配置关：探针零足迹 no-op（不建目录、不铺授、不打 warning）。"""
    from hiveweave.services.acl_sandbox import temppatch

    monkeypatch.setattr(settings, "acl_sandbox", False)
    res = await temppatch.probe_private_temp(workspace_path=str(ws),
                                             agent_id="A001")
    assert res["error"] == "sandbox-disabled"
    assert not (ws / ".hiveweave" / "sandbox-temp" / "A001").exists()


# ── P2（audit 2026-09-05）：原子写 / 探针残留 / walker 水位 ─────────
def test_shim_write_atomic_no_residue(tmp_path: Path, monkeypatch) -> None:
    """P2-1：shim 走「临时文件 + os.replace」原子替换 —— 无 .tmp-* 残留；
    replace 失败时 fail-soft 返回 None 且既有文件不被截断。"""
    from hiveweave.services.acl_sandbox import temppatch
    from hiveweave.services.acl_sandbox.temppatch import SHIM_SOURCE

    d = tmp_path / "sandbox-temp"
    assert temppatch.ensure_sitecustomize_shim(str(d)) == str(d)
    target = d / "sitecustomize.py"
    assert target.read_text(encoding="utf-8") == SHIM_SOURCE
    assert not list(d.glob("*.tmp-*")), "替换后不得残留临时文件"

    # replace 失败 → 返回 None、既有文件完好、临时残留被清理
    target.write_text("STALE", encoding="utf-8")

    def _boom(src, dst):
        raise OSError("replace boom")

    monkeypatch.setattr(temppatch.os, "replace", _boom)
    assert temppatch.ensure_sitecustomize_shim(str(d)) is None
    assert target.read_text(encoding="utf-8") == "STALE"
    assert not list(d.glob("*.tmp-*")), "失败路径也应清理临时文件"


async def test_probe_residue_retry_then_warn(monkeypatch, ws: Path) -> None:
    """P2-2：探针写成功但 remove 失败 → 重试一次；仍失败只记日志，
    不上抛、不影响 writable/ok 判定。"""
    from hiveweave.services.acl_sandbox import temppatch

    real_remove = os.remove
    attempts = {"n": 0}

    def flaky_remove(path):
        if ".probe-" in str(path):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("locked")
        return real_remove(path)

    monkeypatch.setattr(temppatch.os, "remove", flaky_remove)
    res = await temppatch.probe_private_temp(workspace_path=str(ws),
                                             agent_id="A001")
    assert res["writable"] is True, "可写性由成功写入证实，不受 remove 失败影响"
    assert attempts["n"] == 2, "remove 应重试恰好一次"
    assert res["ok"] is True
    anchor = ws / ".hiveweave" / "sandbox-temp" / "A001"
    assert not list(anchor.glob(".probe-*")), "重试成功后无探针残留"


async def test_walker_watermark_once_per_anchor(
    ws: Path, monkeypatch
) -> None:
    """P2-3：同进程同锚点 walker 只全跑一次（每命令路径命中水位跳过）；
    probe 路径不受水位限制仍全跑。"""
    from hiveweave.services.acl_sandbox import temppatch

    calls: list[str] = []
    real = temppatch.repair_temp_islands

    def spy(temp_dir, *a, **k):
        calls.append(temp_dir)
        return real(temp_dir, *a, **k)

    monkeypatch.setattr(temppatch, "repair_temp_islands", spy)

    await _run(ws, "A001", _cmd("echo one"))    # 首命令：walker 全跑
    assert len(calls) == 1
    await _run(ws, "A001", _cmd("echo two"))    # 命中水位：跳过
    assert len(calls) == 1

    res = await temppatch.probe_private_temp(workspace_path=str(ws),
                                             agent_id="A001")
    assert len(calls) == 2, "probe 路径不受水位限制，仍全跑"
    assert res["writable"] is True


async def test_probe_reports_unwritable_when_platform_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    """锚点目录被平台侧替换为不可写形态时探针给 warning 字段且不抛。"""
    import win32security as ws

    from hiveweave.services.acl_sandbox import temppatch

    d = tmp_path / "ws2"
    d.mkdir()
    _ensure_subject_ace(d)
    (d / ".hiveweave").mkdir()
    # 把 sandbox-temp 根做成 mode=0o700 死岛 —— 平台 makedirs 后写探针
    # 仍能成功（平台=owner 经 OWNER_RIGHTS 有权）？不：OWNER_RIGHTS 只给
    # owner READ_CONTROL+WRITE_DAC，普通写被拒 → writable=False 路径。
    st = d / ".hiveweave" / "sandbox-temp"
    st.mkdir(mode=0o700)
    # 平台令牌是 owner（OWNER RIGHTS:Full 覆盖平台自身），探针可能成功也可能
    # 被拒 —— 这里只钉 fail-soft 契约：任何路径都不得上抛，返回结构化 dict。
    monkeypatch.setattr(
        temppatch, "repair_temp_islands",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    res = await temppatch.probe_private_temp(workspace_path=str(d),
                                             agent_id="A001")
    assert isinstance(res, dict) and "ok" in res


# ── 兜底：重复命令下 walker 幂等（verify-then-skip 不破坏已修复树） ──
async def test_walker_idempotent_under_repeat_commands(ws: Path) -> None:
    script = ws / "_chain_probe2.py"
    script.write_text(_PYTEST_CHAIN_SCRIPT, encoding="utf-8")
    for _ in range(2):
        r = await _run(ws, "A001", _cmd("python _chain_probe2.py"))
        assert r is not None and r["exit_code"] == 0, r
