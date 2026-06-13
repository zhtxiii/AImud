"""
死亡恢复例程：鬼门关 → 复活客栈 → 问"回家" → 雪山寺复活 → 恢复。
"""
import time

from config import Colors
from mud import profile
from mud.profile import cmd
from mud.routines.base import Routine, RoutineResult, RoutineContext, \
    OUTCOME_COMPLETED, OUTCOME_FAILED
from mud.routines.navigate import goto, ARRIVED


class DeathRecoveryRoutine(Routine):
    name = "death_recovery"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        # 死亡瞬间服务器常会踢断连接 → 例程可能在登录界面被调用。
        # 先探测：若出现登录提示，直接交回（milestones 会先派 login）
        text = ctx.io.drain(quiet=0.4, deadline=2.0)
        events = profile.detect_events(text)
        if profile.has_event(events, "LOGIN_NAME", "LOGIN_PASSWORD", "LOGIN_TAKEOVER"):
            ctx.char["logged_in"] = False
            return RoutineResult(OUTCOME_FAILED, "连接处于登录界面，需要先登录")
        ctx.refresh_room()
        node = ctx.char.get("location_node") or ""

        if not node.startswith("d/death"):
            # 不在死亡区：可能已复活或误判
            hp = ctx.refresh_hp() or {}
            if hp.get("eff_kee", 0) > 5:
                ctx.char["ghost"] = False
                return RoutineResult(OUTCOME_COMPLETED, "角色并未死亡（误判已纠正）")

        # 1. 走到复活客栈
        if goto(ctx, "death_inn1") != ARRIVED:
            return self.escalate(ctx, "死亡区迷路", f"无法从 {node} 到达复活客栈")

        # 2. 对"长得跟你一样的人"问回家
        my_id = ctx.state.get("credentials", {}).get("id") or ctx.char.get("id", "")
        if not my_id:
            return self.escalate(ctx, "凭据缺失", "不知道自己的 id，无法问话复活")
        revived = False
        for _ in range(3):
            text = ctx.io.request(cmd.ask(my_id, "回家"), quiet=1.0, deadline=10.0)
            time.sleep(2)
            extra = ctx.io.drain(quiet=0.6, deadline=4.0)
            room = ctx.refresh_room()
            if room and ctx.char.get("location_node", "").startswith("d/snow"):
                revived = True
                break
        if not revived:
            return self.escalate(ctx, "复活失败", "ask 回家 未触发复活")

        # 3. 恢复
        ctx.char["ghost"] = False
        ctx.counters["deaths_recovered"] = ctx.counters.get("deaths_recovered", 0) + 1
        sc = ctx.refresh_score()
        ctx.log("复活", f"复活成功，当前经验 {ctx.char.get('exp')}（死亡损失已计）", Colors.YELLOW)

        start = time.time()
        while time.time() - start < 600:
            hp = ctx.refresh_hp() or {}
            if hp.get("kee_pct", 0) >= 80 and hp.get("gin_pct", 0) >= 60:
                break
            if hp.get("force", 0) >= 25:
                ctx.io.request(cmd.exert("recover"), deadline=4.0)
            else:
                time.sleep(8)

        ctx.io.request(cmd.save(), deadline=5.0)
        ctx.checkpoint(force=True)
        return RoutineResult(OUTCOME_COMPLETED,
                             f"复活并恢复完成，exp={ctx.char.get('exp')}")
