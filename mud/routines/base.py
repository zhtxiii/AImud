"""
例程框架：确定性执行器的公共基础设施。
- RoutineResult: 例程统一返回值（outcome 驱动 planner 路由）
- RoutineContext: 注入 MudIO/World/状态/日志/checkpoint/停止标志
- Routine: 基类，PROBE 幂等重入 + escalate 升级 + 关键事件统一处理
"""
import os
import time
from dataclasses import dataclass, field

import config
import persistence
import runtime_control
from config import Colors
from mud import profile
from mud.profile import cmd
from mud.protocol import MudIO, SocketLost
from mud.world import get_world
from nodes.helpers import log_colored

# outcome 取值
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"          # detail 以 "death" 开头表示死亡
OUTCOME_ESCALATE = "escalate"
OUTCOME_RECONNECT = "reconnect"
OUTCOME_STOPPED = "stopped"
OUTCOME_GOAL = "goal_reached"


@dataclass
class RoutineResult:
    outcome: str
    detail: str = ""
    state_updates: dict = field(default_factory=dict)


class RoutineContext:
    """例程运行环境。所有例程通过 ctx 与外界交互。"""

    def __init__(self, state: dict):
        self.state = state
        self.world = get_world()
        self.llm = state.get("llm")
        self.char = state.setdefault("char_status", {})
        self.counters = state.setdefault("counters", {})
        self.exp_history = state.setdefault("exp_history", [])
        self._io_log_path = os.path.join(
            config.LOG_DIR, "system", f"io-{time.strftime('%Y%m%d')}.log")
        self.io = MudIO(state["client"], logger=self._io_logger)
        self._last_checkpoint = 0.0
        self._last_progress = 0.0

    # ------------------------------------------------------------------
    def _io_logger(self, direction: str, text: str):
        try:
            ts = time.strftime("%H:%M:%S")
            with open(self._io_log_path, "a", encoding="utf-8") as f:
                if direction == ">>":
                    f.write(f"[{ts}] >> {text}\n")
                else:
                    f.write(f"[{ts}] << {text[:2000]}\n")
        except OSError:
            pass

    def log(self, tag: str, msg: str, color: str = Colors.CYAN):
        log_colored(tag, msg, color)

    def stop_requested(self) -> bool:
        return runtime_control.stop_requested()

    # ------------------------------------------------------------------
    def checkpoint(self, force: bool = False):
        now = time.time()
        if force or now - self._last_checkpoint >= config.CHECKPOINT_SEC:
            persistence.save_checkpoint(self.state)
            self._last_checkpoint = now

    def record_exp(self, exp: int, force_progress: bool = False):
        """更新经验追踪 + 周期性写 progress.csv。"""
        now = time.time()
        self.char["exp"] = exp
        self.char["updated_at"] = now
        if not self.exp_history or self.exp_history[-1][1] != exp:
            self.exp_history.append([now, exp])
            if len(self.exp_history) > 500:
                del self.exp_history[:len(self.exp_history) - 500]
        if force_progress or now - self._last_progress >= config.SCORE_POLL_SEC:
            rate = persistence.calc_rate_1h(self.exp_history)
            persistence.append_progress(
                exp=exp, rate_1h=rate,
                milestone=self.state.get("milestone", {}).get("id", "?"),
                kee_pct=self.char.get("kee_pct", -1),
                potential=self.char.get("potential", -1),
                deaths=self.counters.get("deaths", 0),
                quests_done=self.counters.get("quests_done", 0),
                quests_skipped=self.counters.get("quests_skipped", 0))
            self._last_progress = now

    # ------------------------------------------------------------------
    def refresh_hp(self) -> dict | None:
        """hp 轮询并合并进 char_status。"""
        text = self.io.request(cmd.hp(), deadline=5.0)
        hp = profile.parse_hp(text)
        if hp:
            self.char.update(hp)
        return hp

    def refresh_score(self) -> dict | None:
        """score 轮询：经验/潜能/杀气/攻防力，全部写入 char_status。"""
        text = self.io.request(cmd.score(), deadline=5.0)
        sc = profile.parse_score(text)
        if sc:
            for k in ("potential", "ap", "dp", "bellicosity", "score"):
                if k in sc:
                    self.char[k] = sc[k]
            self.record_exp(sc["exp"])
        return sc

    def refresh_room(self) -> dict | None:
        """look 并定位当前房间（更新 location_node）。"""
        text = self.io.request(cmd.look(), quiet=0.4, deadline=6.0)
        room = profile.parse_room(text)
        if room:
            node = self.world.locate_by_label(
                room["name"], near=self.char.get("location_node"))
            if node:
                self.char["location_node"] = node
            self.char["room_view"] = room
        return room

    # ------------------------------------------------------------------
    def check_critical(self, events: list[dict]) -> str | None:
        """
        统一处理关键事件。返回 "death" / "robot_check" / None。
        死亡时记录 deaths.log 与计数。
        """
        if profile.has_event(events, "SELF_DEATH", "GHOST_HINT"):
            self.counters["deaths"] = self.counters.get("deaths", 0) + 1
            exp = self.char.get("exp", 0)
            persistence.log_death(
                f"角色死亡！当前里程碑={self.state.get('milestone', {}).get('id')} "
                f"经验≈{exp}（损失约 {exp // 10}） 位置={self.char.get('location_node')}")
            self.checkpoint(force=True)
            return "death"
        if profile.has_event(events, "ROBOT_CHECK"):
            return "robot_check"
        return None

    def try_answer_robot_check(self, text: str) -> bool:
        """审判官算术题（答错4次即死！）：本地解算四则/最大公因数，
        失败再用 LLM 兜底。必须在 20 秒内回答。"""
        import re as _re
        tail = text[-400:]
        # 常见题型: "X 加/减/乘 Y", "X 和 Y 的最大公因数"
        from mud.profile import cn2int
        def num(s):
            return cn2int(s) if not s.isdigit() else int(s)
        m = _re.search(r"([零〇一二两三四五六七八九十百千万\d]+)\s*(加|减|乘|加上|减去|乘以)\s*([零〇一二两三四五六七八九十百千万\d]+)", tail)
        ans = None
        if m:
            a, op, b = num(m.group(1)), m.group(2), num(m.group(3))
            ans = a + b if op.startswith("加") else (a - b if op.startswith("减") else a * b)
        else:
            m = _re.search(r"([零〇一二两三四五六七八九十百千万\d]+)\s*(?:和|与)\s*([零〇一二两三四五六七八九十百千万\d]+)\s*的最大公因数", tail)
            if m:
                ans = _gcd(num(m.group(1)), num(m.group(2)))
        if ans is None and self.llm:
            try:
                res = self.llm.call_with_retry(
                    "提取中文算术题并计算。严格输出 JSON：{\"answer\": <整数>}",
                    tail, json_mode=True,
                    validator=lambda r: isinstance(r, dict) and isinstance(r.get("answer"), int),
                    caller_id="RobotCheck", max_retries=2)
                ans = res["answer"]
            except Exception:
                return False
        if ans is None:
            return False
        self.io.send(cmd.answer(int(ans)))
        return True


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


class Routine:
    """
    例程基类。子类实现 step 逻辑（通常是 while 循环状态机）。
    约定：
    - run() 开头必须 probe()（幂等重入：断线重派后从现状收敛）
    - 每轮循环检查 ctx.stop_requested()
    - SocketLost 不在例程内捕获重连，直接转 OUTCOME_RECONNECT
    """
    name = "base"

    def run(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        try:
            if ctx.stop_requested():
                return RoutineResult(OUTCOME_STOPPED, "收到停止信号")
            return self.execute(ctx, params or {})
        except SocketLost as e:
            ctx.checkpoint(force=True)
            return RoutineResult(OUTCOME_RECONNECT, f"连接断开: {e}")
        except Exception as e:
            ctx.checkpoint(force=True)
            return self.escalate(ctx, "exception",
                                 f"{type(e).__name__}: {e}")

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    def probe(self, ctx: RoutineContext) -> dict | None:
        """同步现状：清空积压输出 → hp → look。返回 hp dict。"""
        leftover = ctx.io.drain(quiet=0.2, deadline=1.5)
        if leftover:
            events = profile.detect_events(leftover)
            crit = ctx.check_critical(events)
            if crit == "death":
                return None
        hp = ctx.refresh_hp()
        ctx.refresh_room()
        return hp

    def escalate(self, ctx: RoutineContext, reason: str, detail: str) -> RoutineResult:
        """升级给 LLM 处理：附带定位与最近输出上下文。"""
        recent = ctx.io.drain(quiet=0.2, deadline=1.0)
        escalation = {
            "routine": self.name,
            "reason": reason,
            "detail": detail,
            "room": ctx.char.get("location_node"),
            "room_label": ctx.world.label_of(ctx.char.get("location_node", "") or ""),
            "recent_output": recent[-1500:],
            "char_status": {k: v for k, v in ctx.char.items() if k != "room_view"},
            "ts": time.time(),
        }
        ctx.log("例程", f"[{self.name}] 升级: {reason} - {detail[:120]}", Colors.RED)
        return RoutineResult(OUTCOME_ESCALATE, f"{reason}: {detail}",
                             {"escalation": escalation})
