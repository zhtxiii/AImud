"""
任务例程：朱鸿雪限时杀怪循环。
领单 → 白名单评估 → 寻路 → 猎杀（结算发生在击杀瞬间）→ 拾取 → 回去续单。
不可达/不可行的单等超时跳过（任务无法主动放弃）。
"""
import json
import time

import config
from config import Colors
from mud import profile
from mud.profile import cmd
from mud.routines.base import Routine, RoutineResult, RoutineContext, \
    OUTCOME_COMPLETED, OUTCOME_FAILED, OUTCOME_STOPPED
from mud.routines.navigate import goto, ARRIVED, DEATH, LOST
from mud.routines.maintain import ensure_supplies, learn_loop, MASTER_ROOM

_whitelist_cache = None


def load_whitelist() -> dict:
    global _whitelist_cache
    if _whitelist_cache is None:
        with open(config.QUEST_WHITELIST_FILE, encoding="utf-8") as f:
            entries = json.load(f)
        _whitelist_cache = {e["target_cn"]: e for e in entries if e.get("kill_id")}
    return _whitelist_cache


class QuestRoutine(Routine):
    name = "quest"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        budget_min = params.get("budget_min", 25)
        deadline = time.time() + budget_min * 60

        self.probe(ctx)
        if ctx.char.get("ghost"):
            return RoutineResult(OUTCOME_FAILED, "death: 角色死亡状态")
        ctx.refresh_score()
        if ctx.char.get("exp", 0) <= 1000:
            return RoutineResult(OUTCOME_FAILED,
                                 f"经验 {ctx.char.get('exp')} 未达任务门槛(>1000)")
        ensure_supplies(ctx)
        whitelist = load_whitelist()
        start_exp = ctx.char.get("exp", 0)
        parse_failures = 0
        nav_failures = 0

        while True:
            if ctx.stop_requested():
                return RoutineResult(OUTCOME_STOPPED, "停止信号")
            if time.time() > deadline:
                ctx.refresh_score()
                done = ctx.counters.get("quests_done", 0)
                return RoutineResult(OUTCOME_COMPLETED,
                                     f"预算用尽：exp {start_exp}→{ctx.char.get('exp')}，"
                                     f"累计完成 {done} 单")

            # --- 潜能余量管理：pot_bonus 入账硬顶 learned+100，
            #     余量过高会被顶掉浪费 → 接单前先把潜能花到 <60 ---
            hp = ctx.refresh_hp() or {}
            if hp.get("potential", 0) > 60:
                if goto(ctx, MASTER_ROOM) == ARRIVED:
                    n = learn_loop(ctx, gin_floor_pct=40, max_rounds=80)
                    if n:
                        ctx.log("任务", f"潜能倾泻：学习 {n} 次（保任务潜能入账）", Colors.CYAN)

            # --- 回朱鸿雪处领单 ---
            nav = goto(ctx, "god2")
            if nav == DEATH:
                return RoutineResult(OUTCOME_FAILED, "death: 往返任务NPC途中死亡")
            if nav in (LOST, "stopped"):
                if nav == "stopped":
                    return RoutineResult(OUTCOME_STOPPED, "停止信号")
                return self.escalate(ctx, "迷路", "无法到达朱鸿雪房间(god2)")

            text, events = ctx.io.request_events(cmd.quest(), quiet=0.8, deadline=8.0)
            grant = profile.parse_quest_grant(text)
            target_cn, limit_sec, t0 = None, 0, time.time()

            if grant:
                target_cn = grant["target_cn"]
                limit_sec = grant["limit_sec"] or 120
                parse_failures = 0
                ctx.log("任务", f"领到任务：杀『{target_cn}』限时 {limit_sec}s", Colors.BLUE)
            else:
                have = profile.has_event(events, "QUEST_HAVE")
                if have:
                    # 已有任务（重入/上轮未完）
                    target_cn = have["groups"][1]
                    tleft = profile.has_event(events, "QUEST_TIME_LEFT")
                    if profile.has_event(events, "QUEST_NOTIME"):
                        # 已超时：重领会清零 tfinished 连胜并扣一半气。
                        # 连胜≥3 档位已升时更要谨慎——但超时单无法继续，只能重领
                        ctx.log("任务", "现有任务已超时，重新领单（连胜清零）", Colors.YELLOW)
                        ctx.counters["quest_streak"] = 0
                        continue
                    limit_sec = profile.duration_cn(tleft["groups"][0]) if tleft else 60
                    ctx.log("任务", f"恢复执行任务：杀『{target_cn}』剩余 {limit_sec}s", Colors.BLUE)
                else:
                    parse_failures += 1
                    if parse_failures >= 3:
                        return self.escalate(ctx, "领单解析失败",
                                             f"quest 回应无法解析: {text[:300]}")
                    time.sleep(2)
                    continue

            # --- RESOLVE: 白名单评估 ---
            entry = whitelist.get(target_cn)
            my_exp = ctx.char.get("exp", 0)
            reason = None
            if entry is None:
                reason = "不在白名单（无法定位）"
            else:
                room = entry["rooms"][0]
                steps = ctx.world.steps_between("u/cloud/god2", room)
                if steps < 0:
                    reason = "目标房间不可达"
                elif steps * 4 + 45 > limit_sec * 0.85:
                    reason = f"时限不足（{steps}步 vs {limit_sec}s）"
                elif entry["exp_min"] > my_exp * 1.5:
                    reason = f"目标过强（exp≈{entry['exp_min']}）"
                elif entry.get("armed") and entry["exp_min"] > my_exp:
                    reason = "目标持武器且不弱于我"
            if reason:
                ctx.counters["quests_skipped"] = ctx.counters.get("quests_skipped", 0) + 1
                ctx.log("任务", f"跳过『{target_cn}』：{reason}，等待超时 {limit_sec}s", Colors.YELLOW)
                self._skip_wait(ctx, t0 + limit_sec + 3, deadline)
                continue

            # --- TRAVEL ---
            nav = goto(ctx, entry["rooms"][0])
            if nav == DEATH:
                return RoutineResult(OUTCOME_FAILED, "death: 前往任务目标途中死亡")
            if nav != ARRIVED:
                nav_failures += 1
                ctx.counters["quests_skipped"] = ctx.counters.get("quests_skipped", 0) + 1
                if nav_failures >= 5:
                    return self.escalate(ctx, "连续导航失败", f"已连续 {nav_failures} 单无法到达目标")
                self._skip_wait(ctx, t0 + limit_sec + 3, deadline)
                continue
            nav_failures = 0

            # --- HUNT ---
            hunt_deadline = min(t0 + limit_sec - 5, deadline + 120)
            found = self._hunt(ctx, entry, hunt_deadline)
            if found == "death":
                return RoutineResult(OUTCOME_FAILED, "death: 搜寻目标时死亡")
            if found != "found":
                ctx.counters["quests_skipped"] = ctx.counters.get("quests_skipped", 0) + 1
                ctx.log("任务", f"『{target_cn}』超时未寻获，跳过", Colors.YELLOW)
                self._skip_wait(ctx, t0 + limit_sec + 3, deadline)
                continue

            # --- KILL ---
            result = self._kill(ctx, entry, t0 + limit_sec)
            if result == "death":
                return RoutineResult(OUTCOME_FAILED, "death: 击杀目标时死亡")
            if result == "done":
                ctx.counters["quests_done"] = ctx.counters.get("quests_done", 0) + 1
                ctx.counters["quest_streak"] = ctx.counters.get("quest_streak", 0) + 1
                ctx.refresh_score()
                ctx.record_exp(ctx.char.get("exp", 0), force_progress=True)
                ctx.checkpoint()
                ctx.log("任务", f"完成『{target_cn}』！累计 {ctx.counters['quests_done']} 单，"
                                f"exp={ctx.char.get('exp')}", Colors.GREEN)
            elif result == "late":
                ctx.log("任务", f"杀了『{target_cn}』但已超时（无奖励）", Colors.YELLOW)
            else:  # aborted
                ctx.counters["quests_skipped"] = ctx.counters.get("quests_skipped", 0) + 1
                self._skip_wait(ctx, t0 + limit_sec + 3, deadline)

    # ------------------------------------------------------------------
    def _hunt(self, ctx, entry: dict, hunt_deadline: float) -> str:
        """在目标房间及 1 跳邻域搜寻目标。返回 found/timeout/death。"""
        kill_id = entry["kill_id"]
        cn = entry["target_cn"]
        home = entry["rooms"][0]
        miss = 0
        while time.time() < hunt_deadline:
            if ctx.stop_requested():
                return "timeout"
            room_view = ctx.refresh_room() or {}
            if any(o.get("id") == kill_id or cn in o.get("cn", "")
                   for o in room_view.get("objects", [])):
                return "found"
            miss += 1
            if miss % 3 == 0:
                # 1 跳邻域扫荡（random_move 的目标会漂）
                here = ctx.char.get("location_node") or home
                for _dir, nxt in ctx.world.neighbors(here):
                    if time.time() >= hunt_deadline:
                        break
                    if ctx.world.danger_of(nxt) or ctx.world.is_forbidden(nxt):
                        continue
                    if goto(ctx, nxt) != ARRIVED:
                        continue
                    rv = ctx.char.get("room_view") or {}
                    if any(o.get("id") == kill_id or cn in o.get("cn", "")
                           for o in rv.get("objects", [])):
                        return "found"
                goto(ctx, home)
            else:
                end = time.time() + 15
                while time.time() < min(end, hunt_deadline):
                    text = ctx.io.drain(quiet=0.4, deadline=2.0)
                    if text and profile.detect_events(text):
                        ev = profile.detect_events(text)
                        if ctx.check_critical(ev) == "death":
                            ctx.char["ghost"] = True
                            return "death"
                    time.sleep(1)
        return "timeout"

    # ------------------------------------------------------------------
    def _kill(self, ctx, entry: dict, expire_ts: float) -> str:
        """真打击杀。返回 done/late/aborted/death。"""
        kill_id = entry["kill_id"]
        cn = entry["target_cn"]
        ctx.io.send(cmd.kill(kill_id))
        baseline = ctx.char.get("kee_integrity", 100)
        last_hp = 0.0
        last_kick = time.time()
        got_reward = False
        target_died = False
        start = time.time()

        while time.time() - start < 300:
            if ctx.stop_requested():
                return "aborted"
            text = ctx.io.drain(quiet=0.4, deadline=3.0)
            events = profile.detect_events(text)
            if ctx.check_critical(events) == "death":
                ctx.char["ghost"] = True
                return "death"
            if profile.has_event(events, "QUEST_DONE", "REWARD"):
                got_reward = True
            died = profile.has_event(events, "SOMEONE_DIED")
            if died and cn in died["match"]:
                target_died = True
            if profile.has_event(events, "SELF_UNCONSCIOUS"):
                # 昏迷醒来后放弃本单
                return "aborted"
            if got_reward or target_died:
                # 拾取（尸体上的钱与物品）
                try:
                    ctx.io.request("get all from corpse", deadline=4.0)
                except Exception:
                    pass
                return "done" if (got_reward and time.time() <= expire_ts + 30) else \
                       ("done" if got_reward else "late")

            now = time.time()
            if now - last_hp >= 8:
                last_hp = now
                hp = ctx.refresh_hp() or {}
                if hp.get("kee_pct", 100) < config.KEE_ABORT_KILL_PCT or \
                        hp.get("kee_integrity", 100) < baseline - 20:
                    ctx.log("任务", "状态不支，撤退弃单", Colors.RED)
                    self._flee_simple(ctx)
                    return "aborted"
            if now - last_kick >= 30:
                last_kick = now
                # 目标可能逃走/打晕未死：补一刀或确认在场
                text, ev = ctx.io.request_events(cmd.kill(kill_id), quiet=0.4, deadline=4.0)
                if profile.has_event(ev, "NO_SUCH_TARGET"):
                    rv = ctx.refresh_room() or {}
                    if not any(o.get("id") == kill_id or cn in o.get("cn", "")
                               for o in rv.get("objects", [])):
                        return "aborted"
        return "aborted"

    def _flee_simple(self, ctx):
        room_view = ctx.char.get("room_view") or {}
        for d in (room_view.get("exits") or ["north", "east", "south", "west"]):
            try:
                text, events = ctx.io.request_events(cmd.go(d), quiet=0.4, deadline=5.0)
            except ValueError:
                continue
            if profile.has_event(events, "SELF_FLEE") or profile.parse_room(text):
                ctx.char["location_node"] = None
                ctx.refresh_room()
                return

    def _skip_wait(self, ctx, until_ts: float, budget_deadline: float):
        """等待任务超时。期间保持恢复与状态轮询，不空转浪费。"""
        until_ts = min(until_ts, time.time() + 600)
        ctx.log("任务", f"等待 {max(0, int(until_ts - time.time()))}s 后重新领单", Colors.CYAN)
        last_poll = 0.0
        while time.time() < until_ts:
            if ctx.stop_requested() or time.time() > budget_deadline + 300:
                return
            text = ctx.io.drain(quiet=0.3, deadline=2.0)
            if text:
                events = profile.detect_events(text)
                if ctx.check_critical(events) == "death":
                    ctx.char["ghost"] = True
                    return
            if time.time() - last_poll >= 60:
                last_poll = time.time()
                hp = ctx.refresh_hp() or {}
                if hp.get("force", 0) >= 25 and hp.get("kee_pct", 100) < 90:
                    ctx.io.request(cmd.exert("recover"), deadline=4.0)
            time.sleep(2)
