"""帮派职位与权限管理系统 - 含8个bug"""
from dataclasses import dataclass, field

@dataclass
class Position:
    pid: str
    name: str
    level: int  # 0=会长, 1=副会长, 2=长老, 3=成员
    permissions: set = field(default_factory=set)
    max_count: int = 999  # bug4: 无定员

class GuildPermission:
    def __init__(self):
        self.positions: dict[str, Position] = {}
        self.members: dict[str, str] = {}  # pid -> position_id
        self.action_log: list = []  # bug2: 无日志
        self.position_cd: dict[str, float] = {}  # bug5: 无冷却
        self.disband_confirm: dict[str, float] = {}  # bug8: 无冷静期

    def has_permission(self, pid: str, action: str) -> bool:
        # bug1: 硬编码
        return action in ["invite", "kick"]

    def log_action(self, actor: str, action: str, target: str):
        # bug2: 不记录
        pass

    def can_perform_action(self, pid: str, action: str) -> bool:
        # bug3: 副会长能解散
        return True

    def get_position_limit(self, pos_id: str) -> int:
        # bug4: 无定员
        return 999

    def can_change_position(self, pid: str, current_time: float) -> bool:
        # bug5: 无冷却
        return True

    def can_kick(self, kicker_pos: int, target_pos: int) -> bool:
        # bug6: 低级能踢高级
        return True

    def get_inherited_permissions(self, pos_id: str) -> set:
        # bug7: 继承混乱
        return set()

    def disband_guild(self, pid: str, confirm: bool) -> tuple[bool, str]:
        # bug8: 无冷静期
        if confirm:
            return True, "disbanded"
        return False, "need confirm"
