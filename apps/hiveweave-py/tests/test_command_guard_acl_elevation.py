"""件3（42 轮双项目报告）：command_guard 补 icacls / takeown deny。

受限令牌 + 能力 SID 沙箱下，agent 跑 icacls（改 DACL）/ takeown（抢
owner）可自提权出沙箱。此前 DEFAULT_BASH_RULES 0 命中这俩命令（首条
`*` → allow 放行）。规则加在表尾，findLast 后规则覆盖前规则。
平台自身 ACL 清理（services/acl_sandbox/cleanup.py）走 win32security
直调，不经 agent bash 护栏 —— 一并锁定该事实。
"""

from __future__ import annotations

from hiveweave.services.command_guard import evaluate_command


class TestAclElevationCommands:
    def test_icacls_denied(self):
        """icacls 改 DACL（/grant /deny /remove /reset）→ deny + ACL 疏通。"""
        v = evaluate_command('icacls "C:\\proj\\file.txt" /grant everyone:F')
        assert v.blocked is True
        assert v.action == "deny"
        assert v.rule == "icacls *"
        assert "ACL" in v.reason

    def test_takeown_denied(self):
        """takeown 抢 owner（WRITE_OWNER）→ deny + ACL 疏通。"""
        v = evaluate_command('takeown /f "C:\\proj\\file.txt"')
        assert v.blocked is True
        assert v.action == "deny"
        assert v.rule == "takeown *"
        assert "ACL" in v.reason

    def test_icacls_denied_after_default_allow_rule(self):
        """回归：规则必须在表尾 —— findLast 下压过首条 `*` → allow。"""
        v = evaluate_command("icacls somefile /reset")
        assert v.blocked is True
        assert v.rule == "icacls *"

    def test_platform_acl_cleanup_bypasses_agent_guard(self):
        """平台侧 ACL 清理不走 agent bash 护栏（win32security 直调，无需豁免）。"""
        import inspect

        from hiveweave.services.acl_sandbox import cleanup

        src = inspect.getsource(cleanup)
        assert "evaluate_command" not in src
        assert "command_guard" not in src
