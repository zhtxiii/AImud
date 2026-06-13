"""
切磋例程：经验主引擎。

ES2 经验机制（combatd.c 逐行核验）：
- fight 切磋：任一方造成有效伤害立刻"承让"散场 → 每周期 2~4 秒重接战，
  期望经验极低（实测 ~37/h），只配做兜底。
- kill 真打：is_killing 状态不散场，每心跳(1s)双方互攻一轮，三条经验通道全开：
  ① 我命中且 my_ap < opp_dp → +1 exp +1 pot（77%）
  ② 我闪避且 my_dp < opp_ap → +1 exp + dodge免费成长（61%）
  ③ 我被重击 random(max_kee+kee) < damage → +1 exp +1 pot
  代价：kill 模式下被命中会累积 wound（eff_kee 下降），需要监控撤退与疗伤。
策略（被殴流）：攻击通道要过 dodge×parry 双判定（~6%/轮），闪避通道单判定
且每次成功闪避 improve dodge（正反馈）→ 让 2~3 个 ap>my_dp 的徒手 NPC kill 我，
每秒被攻 2~3 轮，闪避得经验；伤势/气低于阈值 → go 撤退恢复 → 再战。
注意：NPC 带 chat_msg_combat 的会用聊天替代攻击动作（如妇人），优先选无战斗聊天的。
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
from mud.routines.maintain import ensure_supplies, ensure_weapon, learn_loop, MASTER_ROOM

_ladder_cache = None

# eff_kee 完好度低于该值 → 撤退疗伤（wound 累积警戒线）
EFF_KEE_RETREAT_PCT = 55
# 恢复后回到该完好度才再战（药物可达���）
EFF_KEE_RESUME_PCT = 75


def load_ladder(world) -> list[dict]:
    global _ladder_cache
    if _ladder_cache is None:
        with open(config.SPAR_LADDER_FILE, encoding="utf-8") as f:
            entries = json.load(f)
        _ladder_cache = [
            e for e in entries
            if e.get("id")
            and any(r["room"] in world.nodes for r in e.get("rooms", []))
        ]
    return _ladder_cache


def pick_site(ctx, blacklist: set) -> dict | None:
    """
    实测驱动的目标选择（多臂老虎机）：
    - 有实测速率的目标：用 EWMA 速率排序（exploit）
    - 未实测目标：用理论先验 + 乐观加成（explore）
    静态估算不可靠（NPC 有隐藏 apply/动态武装），实测才是真相。
    """
    ladder = load_ladder(ctx.world)
    rates = ctx.char.get("target_rates", {})
    exp = ctx.char.get("exp", 0)
    my_ap = max(ctx.char.get("ap", 0), exp // 2, 1)
    my_dp = max(ctx.char.get("dp", 0), exp // 2, 1)
    here = ctx.char.get("location_node") or "d/snow/inn"
    now = time.time()
    # 承伤约束：血量 + 招架共同决定能抗多强的对手。
    # 持鸡腿（武器）时 score 防御力 = dodge+parry 全额 → my_dp 反映招架功力；
    # 招架挡住的攻击 = 零伤害 + 61% 经验判定 → parry 成长自动解锁高 ap 野兽群（岩蛭等）
    max_kee = ctx.char.get("eff_kee", 100)
    integ = ctx.char.get("kee_integrity", 100)
    ap_ceiling = max(50, max_kee * 2 + my_dp)
    if integ < 85:
        ap_ceiling = min(ap_ceiling, max(30, max_kee + my_dp // 2))  # 带伤期收紧
    best, best_score = None, -1.0
    for e in ladder:
        opp_ap, opp_dp = e["ap_est"], e["dp_est"]
        if "dummy" in e.get("file", ""):
            # 修炼傀儡：零伤害+心跳发放（exp/500 指数），绝对首选
            for site in e["rooms"]:
                if site["room"] in ctx.world.nodes:
                    return {"entry": e, "room": site["room"], "count": site.get("count", 1)}
        if opp_ap > ap_ceiling:
            continue  # 超出血量承受力
        for site in e["rooms"]:
            room = site["room"]
            key = f"{e['file']}@{room}"
            if key in blacklist:
                continue
            if room not in ctx.world.nodes or ctx.world.danger_of(room) \
                    or ctx.world.is_forbidden(room):
                continue
            dist = ctx.world.steps_between(here, room)
            if dist < 0:
                continue
            meas = rates.get(key)
            if meas and meas.get("rate", 0) < 0:
                continue  # 死亡拉黑（负分）
            if meas and now - meas.get("ts", 0) < 7200 and meas.get("n", 0) >= 1:
                # 新鲜实测（exp/h）
                score = max(meas["rate"], 1.0)
            elif meas and meas.get("n", 0) >= 2 and meas.get("rate", 0) > 50:
                # 过期但多次验证的高分点：衰减 30% 后仍强于先验
                score = meas["rate"] * 0.7
            else:
                # 理论先验：闪避 + 攻击 + 被重击三通道期望
                r = opp_ap / max(my_dp, 1)
                dodge_gain = 0.61 / (1.0 + r) if r > 1.05 else 0.0
                atk_gain = (my_ap / (my_ap + opp_dp)) * 0.77 if my_ap < opp_dp else 0.0
                # 被重击通道：random(max_kee+kee)<damage → 血越薄越易触发；
                # 期望 ≈ 对方命中率 × 重击概率（damage 量级/血池），上限 0.35
                opp_hit = opp_ap / (opp_ap + my_dp)
                tank_gain = opp_hit * min(0.35, 60.0 / max(max_kee * 1.5, 30))
                if dodge_gain + atk_gain + tank_gain <= 0.02:
                    continue
                risk = 1.0 if r <= 3 else (0.5 if r <= 8 else 0.1)
                score = (dodge_gain + atk_gain + tank_gain) * 800 * risk * 1.3
            count = min(site.get("count", 1), config.PARALLEL_SPAR)
            score *= (1 + 0.2 * (count - 1)) / (1 + dist / 20.0)
            if score > best_score:
                best_score = score
                best = {"entry": e, "room": room, "count": site.get("count", 1)}
    return best


def record_target_rate(ctx, entry, room, exp_gain: int, duration_sec: float):
    """记录目标实测速率（EWMA），供 bandit 选择。"""
    if duration_sec < 60:
        return
    rate = exp_gain * 3600.0 / duration_sec
    rates = ctx.char.setdefault("target_rates", {})
    key = f"{entry['file']}@{room}"
    old = rates.get(key)
    if old and old.get("n", 0) > 0:
        alpha = 0.5
        rate = alpha * rate + (1 - alpha) * old["rate"]
        n = old["n"] + 1
    else:
        n = 1
    rates[key] = {"rate": round(rate, 1), "ts": time.time(), "n": n}
    ctx.log("切磋", f"实测速率 {key.split('/')[-1]}: {rate:.0f}/h (n={n})", Colors.CYAN)


def entry_count_for(ctx, entry) -> int:
    """当前房间该 NPC 的实际在场数量（兜底用索引 count）。"""
    rv = ctx.char.get("room_view") or {}
    n = sum(1 for o in rv.get("objects", [])
            if (o.get("id") == entry["id"] or entry["cn"] in o.get("cn", ""))
            and "尸体" not in o.get("cn", ""))
    if n:
        return n
    for site in entry.get("rooms", []):
        if site["room"] == ctx.char.get("location_node"):
            return site.get("count", 1)
    return 1


class SparRoutine(Routine):
    name = "spar"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        target_exp = params.get("target_exp")
        budget_min = params.get("budget_min", 30)
        start_ts = time.time()
        deadline = start_ts + budget_min * 60

        self.probe(ctx)
        if ctx.char.get("ghost"):
            return RoutineResult(OUTCOME_FAILED, "death: 角色死亡状态")
        ctx.refresh_score()
        start_exp = ctx.char.get("exp", 0)
        ensure_supplies(ctx)
        ensure_weapon(ctx)

        bl_raw = ctx.char.get("ladder_blacklist", {})
        if isinstance(bl_raw, list):  # 兼容旧格式
            bl_raw = {k: time.time() + config.SPAR_BLACKLIST_TTL for k in bl_raw}
        blacklist = {k: v for k, v in bl_raw.items() if v > time.time()}
        ctx.char["ladder_blacklist"] = blacklist
        site = None
        site_failures = 0

        while True:
            if ctx.stop_requested():
                self._retreat(ctx)
                return RoutineResult(OUTCOME_STOPPED, "停止信号")
            now = time.time()
            exp = ctx.char.get("exp", 0)
            if target_exp and exp >= target_exp:
                self._retreat(ctx)
                ctx.refresh_score()
                ctx.record_exp(ctx.char.get("exp", exp), force_progress=True)
                return RoutineResult(OUTCOME_COMPLETED,
                                     f"达到目标经验 {ctx.char.get('exp')}（起始 {start_exp}）")
            if now > deadline:
                self._retreat(ctx)
                ctx.refresh_score()
                gained = ctx.char.get("exp", exp) - start_exp
                rate = gained * 3600.0 / max(60.0, now - start_ts)
                ctx.record_exp(ctx.char.get("exp", exp), force_progress=True)
                return RoutineResult(OUTCOME_COMPLETED,
                                     f"预算用尽：{start_exp}→{ctx.char.get('exp')}（≈{rate:.0f}/h）")

            # ---- 选驻点（按当前 ap/dp 重选，跟随成长爬梯） ----
            if site is None:
                ctx.refresh_score()
                ctx.refresh_hp()   # eff_kee 必须新鲜（ap_ceiling 依赖）
                site = pick_site(ctx, blacklist)
                if site is None:
                    # 兜底1：清过期 TTL 再试
                    now2 = time.time()
                    expired = [k for k, v in blacklist.items() if v <= now2]
                    for k in expired:
                        del blacklist[k]
                    if expired:
                        site = pick_site(ctx, blacklist)
                if site is None and blacklist:
                    # 兜底2：忽略 TTL 拉黑（保留死亡拉黑）强选一次
                    ctx.log("切磋", "选点池枯竭，临时忽略 TTL 拉黑重选", Colors.YELLOW)
                    site = pick_site(ctx, set())
                if site is None:
                    # 兜底3：等 5 分钟（对手恢复/重生）再 escalate
                    ctx.log("切磋", "确实无目标，等待 300s 后重试", Colors.YELLOW)
                    end_wait = time.time() + 300
                    while time.time() < end_wait and not ctx.stop_requested():
                        time.sleep(10)
                        ctx.io.drain(quiet=0.2, deadline=0.5)
                    site = pick_site(ctx, blacklist)
                if site is None:
                    return self.escalate(ctx, "梯子断档",
                                         f"无可用陪练（exp={exp}, 拉黑 {len(blacklist)} 处）")
                e = site["entry"]
                ctx.log("切磋", f"驻点 → {e['cn']}({e['id']})×{site['count']} "
                                f"ap≈{e['ap_est']} dp≈{e['dp_est']} @ {site['room']}", Colors.CYAN)

            entry = site["entry"]
            nav = goto(ctx, site["room"])
            if nav == DEATH:
                return RoutineResult(OUTCOME_FAILED, "death: 前往驻点途中死亡")
            if nav != ARRIVED:
                if nav == "stopped":
                    return RoutineResult(OUTCOME_STOPPED, "停止信号")
                blacklist[f"{entry['file']}@{site['room']}"] = time.time() + config.SPAR_BLACKLIST_TTL
                ctx.char["ladder_blacklist"] = blacklist
                site = None
                continue

            # ---- 入场体检：开打前必须健康（防重启带伤直接送死） ----
            hp_pre = ctx.refresh_hp() or {}
            if hp_pre.get("kee_pct", 0) < config.KEE_RESUME_PCT \
                    or hp_pre.get("kee_integrity", 100) < 70 \
                    or hp_pre.get("water", 999) < 10 or hp_pre.get("food", 999) < 10:
                precheck_fails = ctx.char.get("precheck_fails", 0) + 1
                ctx.char["precheck_fails"] = precheck_fails
                if precheck_fails <= 2:
                    ctx.log("切磋", f"入场体检不合格(气{hp_pre.get('kee_pct')}% "
                                    f"完好{hp_pre.get('kee_integrity')}%)，先恢复", Colors.YELLOW)
                if not self._recover(ctx):
                    return self.escalate(ctx, "恢复异常", "入场体检后未能恢复")
                continue
            ctx.char["precheck_fails"] = 0

            # ---- kill 模式战斗循环 ----
            ctx.refresh_score()
            loop_start_exp = ctx.char.get("exp", 0)
            loop_start_ts = time.time()
            result = self._kill_loop(ctx, entry, target_exp, deadline)
            if result == "death":
                # 死亡目标永久拉黑（这个档位打不过，不要再试）
                rates = ctx.char.setdefault("target_rates", {})
                rates[f"{entry['file']}@{site['room']}"] = {
                    "rate": -1000.0, "ts": time.time() + 86400 * 30, "n": 99}
            ctx.refresh_score()
            # 只结算"纯战斗"会话：recovered/rotate 时段混入静养/学习，归因失真
            if result in ("done", "stall", "site_dead"):
                record_target_rate(ctx, entry, site["room"],
                                   ctx.char.get("exp", 0) - loop_start_exp,
                                   time.time() - loop_start_ts)
            if result == "death":
                return RoutineResult(OUTCOME_FAILED, "death: 战斗中死亡")
            if result == "stopped":
                return RoutineResult(OUTCOME_STOPPED, "停止信号")
            if result == "stall":
                return self.escalate(ctx, "速率异常",
                                     f"{config.STALL_SOFT_MIN} 分钟无经验增长")
            if result == "spare":
                blacklist[f"{entry['file']}@{site['room']}"] = time.time() + 300
                ctx.char["ladder_blacklist"] = blacklist
                site = None
                site_failures = 0
                continue
            if result == "site_dead":
                site_failures += 1
                # 短 TTL（5分钟）：对手耗尽/暂离是常态，长拉黑会饿死选点池
                blacklist[f"{entry['file']}@{site['room']}"] = time.time() + 300
                ctx.char["ladder_blacklist"] = blacklist
                site = None
                if site_failures >= 8:
                    return self.escalate(ctx, "梯子断档", "连续 8 个驻点不可用")
                continue
            if result == "rotate":
                site_failures = 0
                site = None  # 速率太低，重新选点（实测已记录，该点自然下沉）
                continue
            if result == "recovered":
                site_failures = 0
                if not ctx.char.get("weapon_ok"):
                    ensure_weapon(ctx)
                if ctx.char.get("potential", 0) >= 95:
                    self._opportunistic_learn(ctx)
                continue
            # "done" → 外层退出判定

    # ------------------------------------------------------------------
    def _kill_loop(self, ctx, entry, target_exp, deadline) -> str:
        """
        kill 持续战斗：发起 → 心跳互攻（收事件）→ eff_kee 低 → 撤退恢复；
        对方死/晕 → 重新 kill 锁定下一副本；副本耗尽 → site_dead。
        返回 death/stopped/stall/site_dead/recovered/done
        """
        tid = entry["id"]
        # 血量决定可承受的攻击者数：max_kee<150 → 1v1；每+120 气可多扛一个
        max_kee = ctx.char.get("eff_kee", 100)
        safe_n = max(1, min(config.PARALLEL_SPAR, max_kee // 120))
        n_attackers = min(entry_count_for(ctx, entry), safe_n)
        last_hp_poll = time.time()
        last_score_poll = time.time()
        last_exp_change = time.time()
        last_exp_val = ctx.char.get("exp", 0)
        engaged = False
        kill_sent_ts = 0.0
        opp_down_count = 0
        session_start = time.time()
        session_start_exp = ctx.char.get("exp", 0)

        while True:
            if ctx.stop_requested():
                self._retreat(ctx)
                return "stopped"
            now = time.time()

            # 发起/重锁定攻击：对 1..k 副本依次 kill（多攻击者=闪避事件倍增）
            if not engaged and now - kill_sent_ts >= 2.0:
                kill_sent_ts = now
                got_any = False
                missing = False
                for k in range(n_attackers):
                    suffix = 0 if k == 0 else k + 1
                    text, events = ctx.io.request_events(
                        cmd.kill(tid, suffix), quiet=0.25, deadline=2.5)
                    if ctx.check_critical(events) == "death":
                        ctx.char["ghost"] = True
                        return "death"
                    if profile.has_event(events, "NO_FIGHT_ROOM"):
                        return "site_dead"
                    if profile.has_event(events, "NO_SUCH_TARGET"):
                        missing = True
                        continue
                    got_any = True
                if not got_any and missing:
                    ctx.refresh_room()
                    rv = ctx.char.get("room_view") or {}
                    alive = [o for o in rv.get("objects", [])
                             if (o.get("id") == tid or entry["cn"] in o.get("cn", ""))
                             and "尸体" not in o.get("cn", "")]
                    if not alive:
                        opp_down_count += 1
                        if opp_down_count >= 2:
                            return "site_dead"
                        t_end = now + 30
                        while time.time() < t_end:
                            if ctx.stop_requested():
                                return "stopped"
                            time.sleep(3)
                            ctx.io.drain(quiet=0.2, deadline=0.6)
                    continue
                engaged = got_any

            # 收战斗输出
            text = ctx.io.drain(quiet=0.3, deadline=1.5)
            if text:
                events = profile.detect_events(text)
                if ctx.check_critical(events) == "death":
                    ctx.char["ghost"] = True
                    return "death"
                if profile.has_event(events, "SELF_UNCONSCIOUS"):
                    if self._wait_revive(ctx) == "death":
                        return "death"
                    self._retreat(ctx)
                    return "recovered"
                if profile.has_event(events, "SOMEONE_DIED", "OPPONENT_DOWN"):
                    engaged = False  # 对方死/晕 → 重新 kill
                if text and ("受伤过重" in text or "奄奄一息" in text or "风中残烛" in text) \
                        and "( 你受伤过重" not in text:
                    # 对手濒死：留活口。先轮换同房其他副本，全部打残才换驻点
                    spare_idx = ctx.char.get("_spare_idx", 1) + 1
                    n_in_room = entry_count_for(ctx, entry)
                    if spare_idx <= n_in_room:
                        ctx.char["_spare_idx"] = spare_idx
                        ctx.log("切磋", f"对手濒死，轮换同房副本 #{spare_idx}/{n_in_room}", Colors.CYAN)
                        self._retreat(ctx)
                        text2, ev2 = ctx.io.request_events(
                            cmd.kill(tid, spare_idx), quiet=0.3, deadline=3.0)
                        if not profile.has_event(ev2, "NO_SUCH_TARGET", "NO_FIGHT_ROOM"):
                            engaged = True
                            continue
                    ctx.char["_spare_idx"] = 1
                    ctx.log("切磋", "全房副本打残，留活口换驻点", Colors.CYAN)
                    self._retreat(ctx)
                    return "spare"  # 正常轮转：短拉黑让对手回血，不计故障

            # 文本级伤情红线：只认自己的伤情报告"( 你受伤过重..."（对手的危重是好事别撤）
            if text and ("( 你受伤过重" in text or "你受伤过重，已经" in text):
                ctx.log("切磋", "伤情红线触发，立即撤退！", Colors.RED)
                self._retreat(ctx)
                if not self._recover(ctx):
                    return "stall"
                return "recovered"

            # hp 轮询：wound 撤退线 + gin/sen 经验效率线
            # （combatd: 经验判定 random(gin%+int)>30/50 → gin 满=77%通过，半血=61%；
            #   skill_power ∝ sen 当前值 → sen 低全面降效）
            if now - last_hp_poll >= config.SPAR_HP_POLL_SEC:
                last_hp_poll = now
                hp = ctx.refresh_hp() or {}
                if hp.get("gin_pct", 100) < 65 or hp.get("sen_pct", 100) < 65:
                    self._retreat(ctx)
                    if not self._recover(ctx):
                        return "stall"
                    return "recovered"
                if hp.get("kee_integrity", 100) < EFF_KEE_RETREAT_PCT or \
                        hp.get("kee_pct", 100) < 60:
                    self._retreat(ctx)
                    if not self._recover(ctx):
                        return "stall"
                    return "recovered"
                if hp.get("food", 999) < config.FOOD_FLOOR // 2 or \
                        hp.get("water", 999) < config.WATER_FLOOR // 2:
                    self._retreat(ctx)
                    ensure_supplies(ctx)
                    return "recovered"

            # score 轮询 + 停滞检测
            if now - last_score_poll >= config.SCORE_POLL_SEC:
                last_score_poll = now
                sc = ctx.refresh_score()
                ctx.checkpoint()
                # 潜能顶满（战斗+1潜能通道条件=余���<100）→ 脱战去倾泻，避免白白溢出
                if sc and ctx.char.get("potential", 0) >= 10**9:  # 加速期禁用倾泻
                    ctx.log("切磋", "潜能顶满 100，脱战倾泻学习", Colors.CYAN)
                    self._retreat(ctx)
                    self._opportunistic_learn(ctx)
                    return "recovered"
                if sc:
                    if sc["exp"] != last_exp_val:
                        last_exp_val = sc["exp"]
                        last_exp_change = now
                    if target_exp and sc["exp"] >= target_exp:
                        self._retreat(ctx)
                        return "done"
                if now > deadline:
                    self._retreat(ctx)
                    return "done"
                if now - last_exp_change > 300:
                    self._retreat(ctx)
                    return "site_dead"
                # 会话速率底线：打满 5 分钟但速率低于底线 → 轮换探索别的目标
                elapsed = now - session_start
                if elapsed > 300:
                    sess_rate = (sc["exp"] - session_start_exp) * 3600.0 / elapsed if sc else 0
                    rates = ctx.char.get("target_rates", {})
                    best_known = max((m.get("rate", 0) for m in rates.values()), default=0)
                    floor = max(60.0, best_known * 0.4)
                    if sess_rate < floor:
                        ctx.log("切磋", f"会话速率 {sess_rate:.0f}/h 低于底线 {floor:.0f}/h，轮换目标",
                                Colors.YELLOW)
                        self._retreat(ctx)
                        return "rotate"
            if now - last_exp_change > config.STALL_SOFT_MIN * 60:
                self._retreat(ctx)
                return "stall"

    # ------------------------------------------------------------------
    def _retreat(self, ctx):
        """脱战：先试 surrender（切磋态有效），kill 态用 go 逃跑。"""
        try:
            text, events = ctx.io.request_events(cmd.surrender(), quiet=0.3, deadline=2.5)
            if profile.has_event(events, "SELF_SURRENDER"):
                return
        except ValueError:
            pass
        for _ in range(3):
            room_view = ctx.char.get("room_view") or {}
            exits = room_view.get("exits") or ["north", "east", "south", "west"]
            for d in exits:
                try:
                    text, events = ctx.io.request_events(cmd.go(d), quiet=0.4, deadline=5.0)
                except ValueError:
                    continue
                if profile.has_event(events, "SELF_FLEE") or profile.parse_room(text):
                    ctx.char["location_node"] = None
                    ctx.refresh_room()
                    return
        ctx.refresh_room()

    def _wait_revive(self, ctx) -> str:
        ctx.log("切磋", "昏迷，等待苏醒...", Colors.YELLOW)
        start = time.time()
        while time.time() - start < 150:
            text = ctx.io.drain(quiet=0.5, deadline=5.0)
            events = profile.detect_events(text)
            if ctx.check_critical(events) == "death":
                ctx.char["ghost"] = True
                return "death"
            if profile.has_event(events, "SELF_REVIVE"):
                return "revived"
        return "revived"

    def _recover(self, ctx) -> bool:
        """恢复：气到 KEE_RESUME_PCT；wound 自然不恢复 → 用药，
        没药则带伤作战（撤退线兜底）。"""
        from mud.routines.maintain import try_apply_medicine
        start = time.time()
        tried_medicine = False
        learned_this_break = False
        last_poll = 0.0
        while time.time() - start < 600:
            if ctx.stop_requested():
                return True
            # 轮询节流：防 hp 风暴（驱动会把超频输入当 flood 踢线）
            gap = time.time() - last_poll
            if gap < 2.0:
                time.sleep(2.0 - gap)
            last_poll = time.time()
            hp = ctx.refresh_hp() or {}
            # 优先级最高：水/食断绝 = 自然恢复完全停摆，必须先补给
            if hp.get("water", 999) < 10 or hp.get("food", 999) < 10:
                ensure_supplies(ctx)
                continue
            kee_ok = hp.get("kee_pct", 0) >= config.KEE_RESUME_PCT \
                and hp.get("gin_pct", 0) >= 85 and hp.get("sen_pct", 0) >= 85
            integ = hp.get("kee_integrity", 100)
            if kee_ok and integ >= EFF_KEE_RESUME_PCT:
                return True
            if not tried_medicine and integ < EFF_KEE_RESUME_PCT:
                tried_medicine = True
                try_apply_medicine(ctx)
                continue
            if integ < 70:
                # 伤未愈：回客栈静养。eff_kee 恢复条件 = 气满后每 tick +1
                ctx.log("切磋", f"重伤未愈（完好度{integ}%），回客栈静养", Colors.YELLOW)
                from mud.routines.navigate import goto as _goto, ARRIVED as _ARR
                if _goto(ctx, "inn") == _ARR:
                    rest_start = time.time()
                    last_rest_poll = 0.0
                    while time.time() - rest_start < 2400:
                        if ctx.stop_requested():
                            return True
                        gap = time.time() - last_rest_poll
                        if gap < 15:
                            time.sleep(15 - gap)
                        last_rest_poll = time.time()
                        hp2 = ctx.refresh_hp() or {}
                        if hp2.get("water", 999) < 20 or hp2.get("food", 999) < 20:
                            ensure_supplies(ctx)
                            continue
                        if hp2.get("kee_integrity", 0) >= 70:
                            return True
                return False
            if kee_ok:
                return True  # 完好度 >=70 且气足：可作战（与体检线一致）
            if hp.get("force", 0) >= 25:
                ctx.io.request(cmd.exert("recover"), quiet=0.3, deadline=4.0)
                continue
            if hp.get("food", 999) < config.FOOD_FLOOR or hp.get("water", 999) < config.WATER_FLOOR:
                ensure_supplies(ctx)
                continue
            if not learned_this_break and hp.get("potential", 0) >= 25 and hp.get("gin_pct", 0) > 60:
                learned_this_break = True
                self._opportunistic_learn(ctx)
                continue
            time.sleep(6)
        return False

    def _opportunistic_learn(self, ctx):
        """潜能充足时学习（learn 自带经验封顶，不会破坏通道）。
        潜能 >=95 时无视距离回武馆倾泻——战斗潜能通道条件是余量<100 才 +1，
        顶满后每次合格命中的潜能全被白白顶掉。"""
        pot = ctx.char.get("potential", 0)
        if pot < 25:
            return
        here = ctx.char.get("location_node")
        if not here:
            return
        if pot < 400 and ctx.world.steps_between(here, "d/snow/schoolhall") > 4:
            return
        hp0 = ctx.refresh_hp() or {}
        if hp0.get("food", 0) < config.FOOD_FLOOR or hp0.get("water", 0) < config.WATER_FLOOR:
            ensure_supplies(ctx)  # 学习先吃饱：water=0 时 heal_up 完全停摆
        if goto(ctx, MASTER_ROOM) == ARRIVED:
            n = learn_loop(ctx, gin_floor_pct=40, max_rounds=120)
            if n:
                ctx.log("切磋", f"恢复间隙学习 {n} 次", Colors.GREEN)
            # learn 大量耗 gin，而经验判定 random(gin%+int)>30/50 依赖 gin 满
            # → 等 gin 回 85% 再返场；水/食归零时等待无意义，立即去补给
            wait_start = time.time()
            while time.time() - wait_start < 300:
                if ctx.stop_requested():
                    break
                hp = ctx.refresh_hp() or {}
                if hp.get("water", 999) < 5 or hp.get("food", 999) < 5:
                    ensure_supplies(ctx)
                    continue
                if hp.get("gin_pct", 0) >= 85:
                    break
                time.sleep(8)
            goto(ctx, here)
