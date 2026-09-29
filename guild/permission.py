"""帮派职位与权限管理系统

修复要点:
1. 权限改为可配置的权限位掩码, 职位关联权限集, 新增职位无需改核心代码
2. 所有职位变动记录操作日志 (谁/什么/时间)
3. 权限按操作粒度检查, 解散帮派只有最高职位能做且需二次确认
4. 每个职位有定员上限
5. 职位变动有冷却时间
6. 严格层级: 低级职位不能操作高级职位
7. 权限继承明确: 高职位包含低职位所有权限
8. 解散帮派有7天冷静期, 全员通知, 可撤销
"""
import time
from dataclasses import dataclass, field
from enum import IntFlag


class Perm(IntFlag):
    """权限位掩码定义, 新操作在此加一位即可"""
    NONE = 0
    CHAT = 1 << 0
    INVITE = 1 << 1
    KICK = 1 << 2
    APPOINT = 1 << 3
    DISBAND = 1 << 4


# 操作名 -> 权限位, 职位通过 permissions 集合关联权限集
ACTION_PERM_MAP: dict[str, Perm] = {
    "chat": Perm.CHAT,
    "invite": Perm.INVITE,
    "kick": Perm.KICK,
    "appoint": Perm.APPOINT,
    "disband": Perm.DISBAND,
}

# 默认职位模板 (可配置, 新增职位只需在此登记)
DEFAULT_POSITIONS: dict[str, dict] = {
    "leader": {"name": "会长", "level": 0, "permissions": {"chat", "invite", "kick", "appoint", "disband"}, "max_count": 1},
    "vice": {"name": "副会长", "level": 1, "permissions": {"chat", "invite", "kick"}, "max_count": 2},
    "elder": {"name": "长老", "level": 2, "permissions": {"chat", "invite"}, "max_count": 5},
    "member": {"name": "成员", "level": 3, "permissions": {"chat"}, "max_count": 100},
}

POSITION_CHANGE_CD = 1.0          # 职位变动冷却 (秒)
DISBAND_COOLDOWN = 7 * 24 * 3600  # 解散冷静期: 7天


@dataclass
class Position:
    pid: str
    name: str
    level: int  # 0=会长, 1=副会长, 2=长老, 3=成员 (数值越小级别越高)
    permissions: set = field(default_factory=set)
    max_count: int = 1  # bug4 fix: 每个职位有定员


class GuildPermission:
    def __init__(self):
        self.positions: dict[str, Position] = {}
        self.members: dict[str, str] = {}  # pid -> position_id
        self.action_log: list[dict] = []
        self.position_cd: dict[str, float] = {}
        self.disband_confirm: dict[str, float] = {}  # pid -> 冷静期开始时间
        self.notifications: list[dict] = []  # 全员通知

    # ---- bug1 fix: 可配置权限位掩码, 按职位权限集判断 ----
    def permission_mask(self, pos_id: str) -> Perm:
        pos = self.positions.get(pos_id)
        if pos is None:
            return Perm.NONE
        mask = Perm.NONE
        for action in pos.permissions:
            mask |= ACTION_PERM_MAP.get(action, Perm.NONE)
        return mask

    def has_permission(self, pid: str, action: str) -> bool:
        pos = self.positions.get(pid)
        if pos is None:
            return False
        return action in pos.permissions

    # ---- bug2 fix: 记录操作日志 (谁/什么/目标/时间) ----
    def log_action(self, actor: str, action: str, target: str,
                   current_time: float | None = None):
        self.action_log.append({
            "actor": actor,
            "action": action,
            "target": target,
            "time": time.time() if current_time is None else current_time,
        })

    # ---- bug3 fix: 按操作粒度检查成员实际职位的权限 ----
    def can_perform_action(self, pid: str, action: str) -> bool:
        pos_id = self.members.get(pid)
        if pos_id is None:
            return False
        return action in self.get_inherited_permissions(pos_id)

    # ---- bug4 fix: 返回职位定员 ----
    def get_position_limit(self, pos_id: str) -> int:
        pos = self.positions.get(pos_id)
        return pos.max_count if pos else 0

    def position_is_full(self, pos_id: str) -> bool:
        count = sum(1 for p in self.members.values() if p == pos_id)
        return count >= self.get_position_limit(pos_id)

    # ---- bug5 fix: 职位变动冷却 ----
    def can_change_position(self, pid: str, current_time: float) -> bool:
        last = self.position_cd.get(pid)
        if last is None:
            return True
        return current_time - last >= POSITION_CHANGE_CD

    # ---- bug6 fix: 严格层级, 低级(数值大)不能操作高级(数值小) ----
    def can_kick(self, kicker_pos: int, target_pos: int) -> bool:
        return kicker_pos < target_pos

    # ---- bug7 fix: 高职位(数值小)继承所有低职位的权限 ----
    def get_inherited_permissions(self, pos_id: str) -> set:
        pos = self.positions.get(pos_id)
        if pos is None:
            return set()
        inherited = set(pos.permissions)
        for other in self.positions.values():
            if other.level >= pos.level:
                inherited |= other.permissions
        return inherited

    # ---- 职位任免: 层级 + 定员 + 冷却 + 日志 ----
    def assign_position(self, actor: str, target: str, pos_id: str,
                        current_time: float | None = None) -> tuple[bool, str]:
        now = time.time() if current_time is None else current_time
        pos = self.positions.get(pos_id)
        if pos is None:
            return False, "position not found"
        actor_pos = self.positions.get(self.members.get(actor, ""))
        if actor_pos is None or actor_pos.level >= pos.level:
            return False, "cannot assign position at or above your level"
        if not self.can_change_position(target, now):
            return False, "position change cooldown"
        if self.position_is_full(pos_id):
            return False, "position is full"
        self.members[target] = pos_id
        self.position_cd[target] = now
        self.log_action(actor, "assign:" + pos_id, target, now)
        return True, "assigned"

    def kick_member(self, kicker: str, target: str,
                    current_time: float | None = None) -> tuple[bool, str]:
        now = time.time() if current_time is None else current_time
        kicker_pos = self.positions.get(self.members.get(kicker, ""))
        target_pos = self.positions.get(self.members.get(target, ""))
        if kicker_pos is None or target_pos is None:
            return False, "member not found"
        if not self.can_kick(kicker_pos.level, target_pos.level):
            return False, "cannot kick equal or higher rank"
        if not self.can_change_position(target, now):
            return False, "position change cooldown"
        del self.members[target]
        self.position_cd[target] = now
        self.log_action(kicker, "kick", target, now)
        return True, "kicked"

    # ---- bug8 fix: 解散需最高职位权限 + 二次确认 + 7天冷静期 + 全员通知 + 可撤销 ----
    def disband_guild(self, pid: str, confirm: bool,
                      current_time: float | None = None) -> tuple[bool, str]:
        if not confirm:
            return False, "need confirm"
        if not self.can_perform_action(pid, "disband"):
            return False, "no permission"
        now = time.time() if current_time is None else current_time
        started = self.disband_confirm.get(pid)
        if started is None:
            # 第一次确认: 进入冷静期并通知全员
            self.disband_confirm[pid] = now
            self._notify_all("guild disband requested, 7-day cooldown started")
            self.log_action(pid, "disband_request", "guild", now)
            return False, "cooldown started: disband in 7 days"
        if now - started < DISBAND_COOLDOWN:
            remaining = DISBAND_COOLDOWN - (now - started)
            return False, f"cooldown: {remaining:.0f}s remaining"
        # 冷静期结束后的二次确认: 正式解散
        del self.disband_confirm[pid]
        self.log_action(pid, "disband", "guild", now)
        return True, "disbanded"

    def cancel_disband(self, pid: str,
                       current_time: float | None = None) -> tuple[bool, str]:
        if pid not in self.disband_confirm:
            return False, "no pending disband"
        now = time.time() if current_time is None else current_time
        del self.disband_confirm[pid]
        self._notify_all("guild disband cancelled")
        self.log_action(pid, "disband_cancel", "guild", now)
        return True, "disband cancelled"

    def _notify_all(self, message: str):
        for member in self.members:
            self.notifications.append({"to": member, "message": message,
                                       "time": time.time()})
