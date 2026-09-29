"""帮派职位与权限管理系统 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guild.permission import GuildPermission, Position

class TestConfigurablePermissions:
    def test_permissions_are_configurable(self):
        gp = GuildPermission()
        gp.positions["p1"] = Position("p1", "elder", 2, permissions={"invite"})
        assert gp.has_permission("p1", "invite") == True
        assert gp.has_permission("p1", "disband") == False  # bug1: 硬编码可能返回True

class TestActionLog:
    def test_position_changes_are_logged(self):
        gp = GuildPermission()
        gp.log_action("p1", "kick", "p2")
        assert len(gp.action_log) > 0  # bug2: 0

class TestDisbandPermission:
    def test_only_leader_can_disband(self):
        gp = GuildPermission()
        gp.positions["leader"] = Position("leader", "会长", 0, permissions={"disband"})
        gp.positions["vp"] = Position("vp", "副会长", 1, permissions=set())
        gp.members["p1"] = "vp"
        assert gp.can_perform_action("p1", "disband") == False  # bug3: True

class TestPositionLimit:
    def test_position_has_max_count(self):
        gp = GuildPermission()
        gp.positions["vp"] = Position("vp", "副会长", 1, max_count=2)
        assert gp.get_position_limit("vp") == 2  # bug4: 999

class TestPositionChangeCooldown:
    def test_cannot_spam_position_changes(self):
        gp = GuildPermission()
        gp.position_cd["p1"] = 100.0
        assert gp.can_change_position("p1", 101.0) == True
        assert gp.can_change_position("p1", 100.5) == False  # bug5: True

class TestKickHierarchy:
    def test_lower_rank_cannot_kick_higher(self):
        gp = GuildPermission()
        assert gp.can_kick(kicker_pos=3, target_pos=0) == False  # bug6: True
        assert gp.can_kick(kicker_pos=0, target_pos=3) == True

class TestPermissionInheritance:
    def test_higher_rank_inherits_lower_permissions(self):
        gp = GuildPermission()
        gp.positions["member"] = Position("member", "成员", 3, permissions={"chat"})
        gp.positions["leader"] = Position("leader", "会长", 0, permissions={"disband"})
        inherited = gp.get_inherited_permissions("leader")
        assert "chat" in inherited  # bug7: 空

class TestDisbandCooldown:
    def test_disband_has_cooldown_period(self):
        gp = GuildPermission()
        result, msg = gp.disband_guild("p1", confirm=True)
        # 应该进入冷静期而不是立即解散
        assert "cooldown" in msg or result == False  # bug8: 立即解散
