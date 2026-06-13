"""
例程注册表：routine_exec 节点按名字实例化。
"""
from mud.routines.login import LoginRoutine
from mud.routines.navigate import NavigateRoutine
from mud.routines.maintain import BootstrapRoutine, MaintainRoutine
from mud.routines.spar import SparRoutine
from mud.routines.quest import QuestRoutine
from mud.routines.death import DeathRecoveryRoutine
from mud.routines.verify import VerifyRoutine

REGISTRY = {
    "login": LoginRoutine,
    "navigate": NavigateRoutine,
    "bootstrap": BootstrapRoutine,
    "maintain": MaintainRoutine,
    "spar": SparRoutine,
    "quest": QuestRoutine,
    "death_recovery": DeathRecoveryRoutine,
    "verify": VerifyRoutine,
}
