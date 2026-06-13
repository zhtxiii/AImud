"""
导航：BFS 路径 + 逐步验证 + RELOCALIZE + 危险房连发协议 + 开门。
提供可复用的 goto() 供其他例程内嵌调用。
"""
import time

import config
from config import Colors
from mud import profile
from mud.profile import cmd
from mud.routines.base import Routine, RoutineResult, RoutineContext, \
    OUTCOME_COMPLETED, OUTCOME_FAILED
from mud.world import ANCHORS

# goto 返回值
ARRIVED = "arrived"
LOST = "lost"
DEATH = "death"
STOPPED = "stopped"


def resolve_dest(dest: str) -> str:
    """锚点名或路径 → 路径。"""
    return ANCHORS.get(dest, dest)


def relocalize(ctx: RoutineContext) -> str | None:
    """look 定位当前房间。返回地图节点路径或 None。"""
    room = ctx.refresh_room()
    if not room:
        return None
    node = ctx.world.locate_by_label(room["name"], near=ctx.char.get("location_node"))
    if node:
        ctx.char["location_node"] = node
    return node


def _wait_for_kee(ctx: RoutineContext, pct: int, timeout: float = 300.0):
    """等待气恢复到指定百分比（穿越危险房前）。"""
    start = time.time()
    while time.time() - start < timeout:
        if ctx.stop_requested():
            return
        hp = ctx.refresh_hp()
        if hp and hp.get("kee_pct", 0) >= pct:
            return
        time.sleep(8)


def _verify_step(ctx: RoutineContext, text: str, expected: dict) -> bool:
    """移动后验证是否到达期望房间。"""
    room = profile.parse_room(text)
    if room and room["name"] == expected["label"]:
        ctx.char["location_node"] = expected["to"]
        ctx.char["room_view"] = room
        return True
    # 输出里没有房间块或名字不符 → 显式 look 再验
    room = ctx.refresh_room()
    if room and ctx.char.get("location_node") == expected["to"]:
        return True
    return False


def goto(ctx: RoutineContext, dest: str, max_replans: int = 3) -> str:
    """
    导航到目标（锚点名或地图路径）。
    返回 ARRIVED / LOST / DEATH / STOPPED。SocketLost 向上抛。
    """
    dest = resolve_dest(dest)
    for _replan in range(max_replans):
        if ctx.stop_requested():
            return STOPPED
        src = ctx.char.get("location_node") or relocalize(ctx)
        if src is None:
            src = relocalize(ctx)
            if src is None:
                return LOST
        if src == dest:
            return ARRIVED
        steps = ctx.world.find_path(src, dest)
        if steps is None:
            ctx.log("导航", f"{src} → {dest} 不可达", Colors.RED)
            return LOST
        result = _walk(ctx, steps)
        if result in (ARRIVED, DEATH, STOPPED):
            return result
        # mismatch → 重定位后重新规划
    return LOST


def _walk(ctx: RoutineContext, steps: list[dict]) -> str:
    i = 0
    door_retries = 0
    while i < len(steps):
        if ctx.stop_requested():
            return STOPPED
        step = steps[i]

        if step.get("danger") == "run_through" and i + 1 < len(steps):
            # 危险房连发协议：满气进入，进入后不停留立刻走下一步
            _wait_for_kee(ctx, 80)
            ctx.io.send(cmd.go(step["dir"]))
            time.sleep(0.15)
            nxt = steps[i + 1]
            text = ctx.io.request(cmd.go(nxt["dir"]), quiet=0.4, deadline=6.0)
            events = profile.detect_events(text)
            crit = ctx.check_critical(events)
            if crit == "death":
                ctx.char["ghost"] = True
                return DEATH
            # 被缠住：还在危险房 → 连续尝试朝出口方向逃（go 即逃跑）
            for _retry in range(3):
                if _verify_step(ctx, text, nxt):
                    break
                if ctx.char.get("location_node") == step["to"]:  # 卡在危险房
                    ctx.log("导航", "被缠住，继续往出口方向逃...", Colors.YELLOW)
                    text = ctx.io.request(cmd.go(nxt["dir"]), quiet=0.4, deadline=6.0)
                    events = profile.detect_events(text)
                    if ctx.check_critical(events) == "death":
                        ctx.char["ghost"] = True
                        return DEATH
                else:
                    return "mismatch"
            else:
                return "mismatch"
            i += 2
            continue

        text = ctx.io.request(cmd.go(step["dir"]), quiet=0.35, deadline=6.0)
        events = profile.detect_events(text)
        crit = ctx.check_critical(events)
        if crit == "death":
            ctx.char["ghost"] = True
            return DEATH

        door = profile.has_event(events, "DOOR_CLOSED")
        if door and door_retries < 2:
            door_retries += 1
            ctx.io.request(f"open {step['dir']}", deadline=4.0)
            continue  # 重试同一步
        door_retries = 0

        if profile.has_event(events, "CONFUSED_CMD", "NO_EXIT"):
            ctx.log("导航", f"方向 {step['dir']} 无效，重定位", Colors.YELLOW)
            relocalize(ctx)
            return "mismatch"

        if not _verify_step(ctx, text, step):
            ctx.log("导航", f"步进验证失败（期望 {step['label']}），重定位", Colors.YELLOW)
            return "mismatch"
        i += 1
    return ARRIVED


class NavigateRoutine(Routine):
    """独立导航任务（params: {dest: 锚点名或路径}）。"""
    name = "navigate"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        dest = params.get("dest", "")
        if not dest:
            return RoutineResult(OUTCOME_FAILED, "未指定目的地")
        self.probe(ctx)
        if ctx.char.get("ghost"):
            return RoutineResult(OUTCOME_FAILED, "death: 角色处于死亡状态")
        result = goto(ctx, dest)
        if result == ARRIVED:
            ctx.checkpoint(force=True)
            return RoutineResult(OUTCOME_COMPLETED, f"已到达 {dest}")
        if result == DEATH:
            return RoutineResult(OUTCOME_FAILED, "death: 导航途中死亡")
        if result == STOPPED:
            from mud.routines.base import OUTCOME_STOPPED
            return RoutineResult(OUTCOME_STOPPED, "停止信号")
        return self.escalate(ctx, "迷路", f"无法到达 {dest}（当前 {ctx.char.get('location_node')}）")
