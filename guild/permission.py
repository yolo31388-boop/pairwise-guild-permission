"""帮派职位与权限管理系统

修复内容:
1. 权限改为可配置的权限位掩码, 职位关联权限集, 新职位/新权限无需改核心代码
2. 所有职位变动记录操作日志 (谁/什么/目标/时间)
3. 权限按操作粒度检查, 解散帮派仅最高职位且需二次确认
4. 每个职位有定员上限
5. 职位变动有冷却时间
6. 严格层级: 低级职位不能操作高级职位
7. 权限继承明确: 高职位(level 小)包含所有低职位的权限
8. 解散帮派有 7 天冷静期 + 全员通知, 可撤销
"""
import time
from dataclasses import dataclass, field

# fix1: 可配置的权限位掩码注册表, 新权限只需注册一个位, 不改核心逻辑
DEFAULT_PERMISSION_BITS: dict[str, int] = {
    "invite": 1 << 0,
    "kick": 1 << 1,
    "chat": 1 << 2,
    "promote": 1 << 3,
    "demote": 1 << 4,
    "manage_positions": 1 << 5,
    "disband": 1 << 6,
}

POSITION_CHANGE_COOLDOWN = 1.0          # fix5: 职位变动冷却(秒)
DISBAND_COOLDOWN = 7 * 24 * 3600.0      # fix8: 解散冷静期 7 天


@dataclass
class Position:
    pid: str
    name: str
    level: int  # 0=会长, 1=副会长, 2=长老, 3=成员; 数值越小职位越高
    permissions: set = field(default_factory=set)
    max_count: int = 1  # fix4: 每个职位默认定员 1, 可配置


class GuildPermission:
    def __init__(self, permission_bits: dict[str, int] | None = None):
        # fix1: 权限位掩码可注入配置, 默认使用内置注册表
        self.permission_bits: dict[str, int] = dict(
            permission_bits if permission_bits is not None else DEFAULT_PERMISSION_BITS
        )
        self.positions: dict[str, Position] = {}
        self.members: dict[str, str] = {}  # pid -> position_id
        self.action_log: list[dict] = []
        self.position_cd: dict[str, float] = {}  # pid -> 上次职位变动时间
        self.disband_confirm: dict[str, float] = {}  # guild 标识 -> 冷静期开始时间
        self.notifications: list[dict] = []  # 全员通知记录

    # ---------- fix1: 可配置权限位掩码 ----------

    def register_permission(self, action: str, bit: int | None = None) -> int:
        """注册新权限位, 返回该权限的位掩码。无需修改核心代码即可扩展权限。"""
        if action in self.permission_bits:
            return self.permission_bits[action]
        if bit is None:
            used = self.permission_bits.values()
            bit = 1 << max(v.bit_length() for v in used) if used else 1
            while bit in used:
                bit <<= 1
        self.permission_bits[action] = bit
        return bit

    def permission_mask(self, actions) -> int:
        """把权限名集合转换为位掩码, 未注册的权限自动注册。"""
        mask = 0
        for action in actions:
            mask |= self.register_permission(action)
        return mask

    def _resolve_position(self, pid: str) -> Position | None:
        """pid 可以是玩家 id (经 members 映射) 或职位 id。"""
        pos_id = self.members.get(pid, pid)
        return self.positions.get(pos_id)

    def has_permission(self, pid: str, action: str) -> bool:
        # fix1+fix3: 按操作粒度检查职位关联的权限集(含继承), 不再硬编码
        pos = self._resolve_position(pid)
        if pos is None:
            return False
        inherited = self.get_inherited_permissions(pos.pid)
        return action in inherited

    # ---------- fix2: 操作日志 ----------

    def log_action(self, actor: str, action: str, target: str,
                   timestamp: float | None = None):
        # fix2: 记录 谁/什么/目标/时间
        self.action_log.append({
            "actor": actor,
            "action": action,
            "target": target,
            "time": time.time() if timestamp is None else timestamp,
        })

    # ---------- fix3: 操作粒度权限检查 ----------

    def can_perform_action(self, pid: str, action: str) -> bool:
        pos = self._resolve_position(pid)
        if pos is None:
            return False
        if action == "disband":
            # fix3: 解散帮派只有最高职位(level 最小)能做
            top_level = min((p.level for p in self.positions.values()),
                            default=0)
            if pos.level != top_level:
                return False
        return self.has_permission(pid, action)

    # ---------- fix4: 职位定员 ----------

    def get_position_limit(self, pos_id: str) -> int:
        pos = self.positions.get(pos_id)
        return pos.max_count if pos is not None else 0

    def position_vacancy(self, pos_id: str) -> bool:
        current = sum(1 for p in self.members.values() if p == pos_id)
        return current < self.get_position_limit(pos_id)

    # ---------- fix5: 职位变动冷却 ----------

    def can_change_position(self, pid: str, current_time: float) -> bool:
        last = self.position_cd.get(pid)
        if last is None:
            return True
        return current_time - last >= POSITION_CHANGE_COOLDOWN

    def change_position(self, actor: str, target: str, new_pos_id: str,
                        current_time: float | None = None) -> tuple[bool, str]:
        """任免职位: 冷却 + 定员 + 层级 + 日志 一体化校验。"""
        now = time.time() if current_time is None else current_time
        actor_pos = self._resolve_position(actor)
        target_pos = self._resolve_position(target)
        new_pos = self.positions.get(new_pos_id)
        if actor_pos is None or new_pos is None:
            return False, "invalid position"
        # fix6: 低级不能操作高级
        if target_pos is not None and not self.can_kick(actor_pos.level,
                                                        target_pos.level):
            return False, "cannot modify higher or equal rank"
        if new_pos.level <= actor_pos.level:
            return False, "cannot grant rank above or equal to self"
        # fix5: 冷却
        if not self.can_change_position(target, now):
            return False, "position change cooldown"
        # fix4: 定员
        if not self.position_vacancy(new_pos_id):
            return False, "position full"
        self.members[target] = new_pos_id
        self.position_cd[target] = now
        self.log_action(actor, "change_position", target, timestamp=now)
        return True, "ok"

    # ---------- fix6: 严格层级 ----------

    def can_kick(self, kicker_pos: int, target_pos: int) -> bool:
        # level 数值越小职位越高; 只有更高职位才能踢/操作低职位
        return kicker_pos < target_pos

    # ---------- fix7: 明确的权限继承 ----------

    def get_inherited_permissions(self, pos_id: str) -> set:
        """高职位(level 小)继承所有低职位(level 大)的权限。"""
        pos = self.positions.get(pos_id)
        if pos is None:
            return set()
        inherited = set(pos.permissions)
        for other in self.positions.values():
            if other.level >= pos.level:
                inherited |= other.permissions
        return inherited

    # ---------- fix8: 解散冷静期 + 二次确认 + 全员通知 + 可撤销 ----------

    def disband_guild(self, pid: str, confirm: bool,
                      current_time: float | None = None) -> tuple[bool, str]:
        now = time.time() if current_time is None else current_time
        if not confirm:
            return False, "need confirm"  # 二次确认
        if not self.can_perform_action(pid, "disband"):
            return False, "no permission"
        started = self.disband_confirm.get("guild")
        if started is None:
            # 进入 7 天冷静期并通知全员, 期间可撤销
            self.disband_confirm["guild"] = now
            for member_pid in self.members:
                self.notifications.append({
                    "pid": member_pid,
                    "type": "disband_cooldown",
                    "time": now,
                })
            self.log_action(pid, "disband_request", "guild", timestamp=now)
            return False, "cooldown started: 7 days"
        if now - started < DISBAND_COOLDOWN:
            return False, "cooldown"
        self.log_action(pid, "disband", "guild", timestamp=now)
        return True, "disbanded"

    def cancel_disband(self, pid: str) -> tuple[bool, str]:
        """冷静期内可撤销解散。"""
        if "guild" not in self.disband_confirm:
            return False, "no pending disband"
        if not self.can_perform_action(pid, "disband"):
            return False, "no permission"
        del self.disband_confirm["guild"]
        for member_pid in self.members:
            self.notifications.append({
                "pid": member_pid,
                "type": "disband_cancelled",
                "time": time.time(),
            })
        self.log_action(pid, "disband_cancel", "guild")
        return True, "disband cancelled"
