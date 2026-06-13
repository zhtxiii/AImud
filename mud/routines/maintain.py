"""
维护例程：补给（食水）、疗伤、学习技能、save。
BootstrapRoutine —— M0 开局立足一条龙。
MaintainRoutine —— M2 周期性全面维护。
ensure_supplies / learn_loop 供 Spar/Quest 内嵌复用。
"""
import time

import config
from config import Colors
from mud import profile
from mud.profile import cmd
from mud.routines.base import Routine, RoutineResult, RoutineContext, \
    OUTCOME_COMPLETED, OUTCOME_FAILED
from mud.routines import navigate
from mud.routines.navigate import goto, ARRIVED

# 柳淳风（封山剑派，schoolhall），id "master"；注意他持剑，绝不可切磋
MASTER_ID = "master"
MASTER_ROOM = "schoolhall"
# learn.c 自带经验封顶（martial 技能 level³/10 ≤ combat_exp），学到 cap 自动停，
# 技能水平始终与经验匹配 → 攻防通道两边都保持"以弱对强"的可达区间。
# 用户经验（机制核验成立）：
# - dodge 千万别学：闪避/招架经验条件都是 dodge_dp < opp_ap，dodge 高了通道全关
# - parry 往死里学：判定条件不看 parry，招架成功率↑ = 挡伤害 + 每次成功招架 61% +1exp
# - force 内功：max_force 抬 max_kee 上限 + exert recover 回气
# - 攻击技能（unarmed 等）不学：持"鸡腿骨头"(hammer 类) 当武器，hammer 永远不学
#   → ap 锁定 exp/2 低位 → 命中时 ap<dp 恒成立 → 每次命中 77% +1exp+1pot
LEARN_SKILLS = ["force", "fonxanforce", "parry"]
FORCE_SPECIAL = "fonxanforce"


def buy_and_consume(ctx: RoutineContext) -> bool:
    """在饮风客栈向店小二买食物/水并进食。前提：已在 inn。返回是否补到位。"""
    # 地上常驻金币（99 个 1 文的铜钱对象，必须 get all 才能全捡）
    ctx.io.request("get all", deadline=5.0)

    for _ in range(5):
        hp = ctx.refresh_hp() or {}
        food_ok = hp.get("food", 0) >= config.FOOD_FLOOR
        water_ok = hp.get("water", 0) >= config.WATER_FLOOR
        if food_ok and water_ok:
            return True
        if not food_ok:
            text, events = ctx.io.request_events(cmd.buy("dumpling", "waiter"), deadline=5.0)
            if profile.has_event(events, "NO_MONEY"):
                ctx.io.request("get all", deadline=4.0)
                text, events = ctx.io.request_events(cmd.buy("dumpling", "waiter"), deadline=5.0)
                if profile.has_event(events, "NO_MONEY"):
                    return False
            ctx.io.request(cmd.eat("dumpling"), deadline=4.0)
            ctx.io.request(cmd.eat("dumpling"), deadline=4.0)
        if not water_ok:
            text, events = ctx.io.request_events(cmd.buy("wineskin", "waiter"), deadline=5.0)
            if profile.has_event(events, "NO_MONEY"):
                return False
            for _d in range(6):
                ctx.io.request(cmd.drink("wineskin"), deadline=4.0)
    hp = ctx.refresh_hp() or {}
    return hp.get("food", 0) >= config.FOOD_FLOOR and hp.get("water", 0) >= config.WATER_FLOOR


def ensure_weapon(ctx: RoutineContext) -> bool:
    """确保手持"鸡腿骨头"（hammer 类，永不学 hammer → ap 恒低 → 命中经验通道常开）。
    前提不限位置；没有骨头时需在客栈（买鸡腿吃光）。返回是否持有武器。"""
    text, events = ctx.io.request_events(cmd.wield("bone"), deadline=4.0)
    if "没有这样东西" not in text and not profile.has_event(events, "CONFUSED_CMD"):
        ctx.char["weapon_ok"] = True
        return True
    # 没有骨头 → 买鸡腿吃光
    if ctx.char.get("location_node") != "d/snow/inn":
        if goto(ctx, "inn") != ARRIVED:
            return False
    text, events = ctx.io.request_events(cmd.buy("chicken leg", "waiter"), deadline=5.0)
    if profile.has_event(events, "NO_MONEY"):
        ctx.io.request("get all", deadline=4.0)
        text, events = ctx.io.request_events(cmd.buy("chicken leg", "waiter"), deadline=5.0)
        if profile.has_event(events, "NO_MONEY"):
            ctx.log("维护", "买不起鸡腿（30文），暂以徒手作战", Colors.YELLOW)
            return False
    for _ in range(5):  # food_remaining=4，吃光自动变骨头
        text = ctx.io.request(cmd.eat("chicken"), deadline=4.0)
        if "骨头" in text or "吃光" in text or "已经吃完" in text:
            break
    text, events = ctx.io.request_events(cmd.wield("bone"), deadline=4.0)
    ok = "没有这样东西" not in text
    ctx.char["weapon_ok"] = ok
    if ok:
        ctx.log("维护", "已装备鸡腿骨头（hammer 类，锁低 ap 保经验通道）", Colors.GREEN)
    return ok


def ensure_supplies(ctx: RoutineContext) -> bool:
    """食水低于下限时回客栈补给。返回是否满足。可能改变位置！"""
    hp = ctx.refresh_hp() or {}
    if hp.get("food", 999) >= config.FOOD_FLOOR and hp.get("water", 999) >= config.WATER_FLOOR:
        return True
    ctx.log("维护", f"食水不足(food={hp.get('food')},water={hp.get('water')})，回客栈补给", Colors.YELLOW)
    if goto(ctx, "inn") != ARRIVED:
        return False
    return buy_and_consume(ctx)


def learn_loop(ctx: RoutineContext, master_id: str = MASTER_ID,
               skills: list = None, gin_floor_pct: int = 35,
               max_rounds: int = 120) -> int:
    """
    向在场老师学习技能直到：潜能耗尽/精不足/全部技能学不动。
    前提：已在老师房间。返回成功学习次数（近似）。
    """
    skills = skills or LEARN_SKILLS
    learned = 0
    dead_skills = set()
    rounds = 0
    while rounds < max_rounds:
        if ctx.stop_requested():
            break
        hp = ctx.refresh_hp() or {}
        if hp.get("potential", 0) <= 0:
            break
        if hp.get("gin_pct", 100) < gin_floor_pct:
            break
        progressed = False
        for skill in skills:
            if skill in dead_skills:
                continue
            rounds += 1
            text, events = ctx.io.request_events(
                cmd.learn(skill, master_id), quiet=0.3, deadline=5.0)
            if profile.has_event(events, "LEARN_NO_POT"):
                return learned
            if profile.has_event(events, "TOO_TIRED_LEARN"):
                return learned
            if profile.has_event(events, "LEARN_CANNOT"):
                dead_skills.add(skill)
                continue
            learned += 1
            progressed = True
        if not progressed:
            break
    return learned


STUDY_BOOK_SOURCE = "d/choyin/bridge2"   # 书生×2 处（携带识字书，可杀取）


def study_literate(ctx: RoutineContext, rounds: int = 30) -> int:
    """识字回路：study 识字书刷 literate（每10级 +2 int 永久提升）。
    书没了→去书生处 kill 补书。sen 不足自动停。返回 study 次数。"""
    done = 0
    for _ in range(rounds):
        if ctx.stop_requested():
            break
        hp = ctx.refresh_hp() or {}
        if hp.get("sen_pct", 0) < 50:
            break
        text = ctx.io.request(cmd.study("book"), deadline=4.0)
        if "没有这样东西" in text:
            if not _fetch_book(ctx):
                break
            continue
        if "太浅" in text:   # literate 已到书的 max_skill(50)
            ctx.char["literate_capped"] = True
            break
        if "疲倦" in text or "经验不足" in text or "没有办法" in text:
            break
        done += 1
    return done


def _fetch_book(ctx: RoutineContext) -> bool:
    """去 choyin 桥头杀书生拾取识字书。书生 exp=10、wimpy=100（速逃不致命）。"""
    from mud.routines.navigate import goto as _goto, ARRIVED as _ARR
    ctx.log("维护", "识字书缺失，去书生处取书", Colors.YELLOW)
    if _goto(ctx, STUDY_BOOK_SOURCE) != _ARR:
        return False
    for _ in range(3):
        text, events = ctx.io.request_events(cmd.kill("scholar"), quiet=0.5, deadline=6.0)
        if profile.has_event(events, "NO_SUCH_TARGET"):
            return False
        # 书生 wimpy=100 一被打就逃，追杀意义不大；等他回来或打到掉书
        end = time.time() + 30
        while time.time() < end:
            text = ctx.io.drain(quiet=0.4, deadline=2.0)
            ev = profile.detect_events(text)
            if ctx.check_critical(ev) == "death":
                return False
            if profile.has_event(ev, "SOMEONE_DIED"):
                ctx.io.request("get all from corpse", deadline=4.0)
                ctx.io.request("get book", deadline=3.0)
                t2 = ctx.io.request(cmd.study("book"), deadline=4.0)
                return "没有这样东西" not in t2
        ctx.refresh_room()
        rv = ctx.char.get("room_view") or {}
        if not any(o.get("id") == "scholar" for o in rv.get("objects", [])):
            return False
    return False


def exercise_force(ctx: RoutineContext, rounds: int = 20) -> int:
    """气→内力转化（exercise）。内力是 exert recover 续航闭环的源头。
    条件：sen/gin ≥70%、气充足。返回成功次数。"""
    done = 0
    for _ in range(rounds):
        if ctx.stop_requested():
            break
        hp = ctx.refresh_hp() or {}
        if hp.get("kee_pct", 0) < 70 or hp.get("gin_pct", 0) < 70:
            break
        text = ctx.io.request(cmd.exercise(30), deadline=4.0)
        if "无法" in text or "太少" in text:
            break
        done += 1
    return done


def wash_bellicosity(ctx: RoutineContext) -> bool:
    """杀气>80 时去雪山寺捐钱洗杀气（kill 流杀气会涨：+1/杀，
    杀气/40>cps 时会被 NPC berserk 主动攻击）。"""
    bell = ctx.char.get("bellicosity", 0)
    if bell <= 80:
        return True
    ctx.log("维护", f"杀气 {bell} 过高，去雪山寺捐款", Colors.YELLOW)
    if goto(ctx, "snow_temple") != ARRIVED:
        return False
    for _ in range(5):
        text = ctx.io.request(cmd.give(200, "coin", "keeper"), deadline=5.0)
        if "多谢" not in text:
            break
        sc = ctx.refresh_score() or {}
        if sc.get("bellicosity", 0) <= 30:
            break
    return True


def try_apply_medicine(ctx: RoutineContext) -> bool:
    """受伤时买药治疗。金疮药 2000 文，钱不够时记录冷却避免反复跑药铺。
    wound 也会被 heal_up 缓慢自然恢复（气满后 eff_kee +1/tick），等待可替代。"""
    hp = ctx.refresh_hp() or {}
    if not hp.get("wounded"):
        return True
    cooldown_until = ctx.char.get("medicine_cooldown_ts", 0)
    if time.time() < cooldown_until:
        return False
    ctx.log("维护", "检测到伤势，尝试买药治疗", Colors.YELLOW)
    if goto(ctx, "herbshop") != ARRIVED:
        ctx.char["medicine_cooldown_ts"] = time.time() + 1800
        return False
    # 药铺掌柜 杨掌柜 id=({"herbalist yang","yang"})
    text, events = ctx.io.request_events(cmd.buy("medicine", "yang"), deadline=5.0)
    if profile.has_event(events, "NO_MONEY") or "你要跟谁买" in text:
        # 金疮药 2000 文，前期买不起 → 30 分钟内不再尝试（靠自然恢复 +1 eff/tick）
        ctx.log("维护", "买不到金疮药（2000文/掌柜不在），靠自然恢复", Colors.YELLOW)
        ctx.char["medicine_cooldown_ts"] = time.time() + 1800
        return False
    ctx.io.request(cmd.apply_medicine(), deadline=5.0)
    hp2 = ctx.refresh_hp() or {}
    if hp2.get("wounded"):
        ctx.char["medicine_cooldown_ts"] = time.time() + 900  # 没治好也别死磕
    return not hp2.get("wounded")


class BootstrapRoutine(Routine):
    """M0 开局立足：捡钱→补给→wimpy→拜师→学技能→enable force→save。"""
    name = "bootstrap"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        self.probe(ctx)
        if ctx.char.get("ghost"):
            return RoutineResult(OUTCOME_FAILED, "death: 角色死亡状态")

        # 1. 回客栈补给（也校准位置）；没钱时等地板金币 reset 刷新（约 ≤800s）
        if goto(ctx, "inn") != ARRIVED:
            return self.escalate(ctx, "迷路", "无法回到出生客栈")
        supply_deadline = time.time() + 900
        while not buy_and_consume(ctx):
            if time.time() > supply_deadline:
                ctx.log("维护", "补给等待超时（钱不够），先继续流程", Colors.YELLOW)
                break
            if ctx.stop_requested():
                break
            ctx.log("维护", "钱不够，等待 60s 后重试（等金币刷新）", Colors.YELLOW)
            time.sleep(60)
            ctx.io.request("get all", deadline=4.0)

        # 2. wimpy 兜底
        ctx.io.request(cmd.set_wimpy(config.WIMPY_PCT), deadline=4.0)

        # 3. 拜师柳淳风
        if goto(ctx, MASTER_ROOM) != ARRIVED:
            return self.escalate(ctx, "迷路", "无法到达武馆大厅")
        text, events = ctx.io.request_events(cmd.apprentice(MASTER_ID), quiet=0.6, deadline=6.0)
        ctx.log("维护", f"拜师回应: {text[:100]}", Colors.CYAN)

        # 4. 学习基础技能（行为验证拜师是否成功）
        learned = learn_loop(ctx)
        if learned == 0:
            # 试一次最基础的 dodge 判断是否被拒
            _t, ev2 = ctx.io.request_events(cmd.learn("dodge", MASTER_ID), deadline=5.0)
            if profile.has_event(ev2, "LEARN_CANNOT"):
                return self.escalate(ctx, "拜师失败", f"柳淳风不肯教学。拜师回应片段: {text[:200]}")
        ctx.log("维护", f"学习完成（{learned} 次）", Colors.GREEN)

        # 5. enable force（为 exert recover 做准备；失败无害）
        try:
            ctx.io.request(cmd.enable("force", FORCE_SPECIAL), deadline=4.0)
        except ValueError:
            pass

        # 6. 装备鸡腿骨头武器（锁低 ap 的核心技巧）
        goto(ctx, "inn")
        ensure_weapon(ctx)

        # 7. save
        ctx.io.request(cmd.save(), deadline=5.0)

        ctx.char["bootstrap_done"] = True
        ctx.refresh_score()
        ctx.checkpoint(force=True)
        return RoutineResult(OUTCOME_COMPLETED,
                             f"开局立足完成：学习 {learned} 次，exp={ctx.char.get('exp', 0)}")


class MaintainRoutine(Routine):
    """周期性维护：补给→疗伤→学习→save。"""
    name = "maintain"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        self.probe(ctx)
        if ctx.char.get("ghost"):
            return RoutineResult(OUTCOME_FAILED, "death: 角色死亡状态")

        ensure_supplies(ctx)
        ensure_weapon(ctx)
        try_apply_medicine(ctx)
        wash_bellicosity(ctx)

        learned = 0
        hp = ctx.refresh_hp() or {}
        if hp.get("potential", 0) > 0:
            if goto(ctx, MASTER_ROOM) == ARRIVED:
                learned = learn_loop(ctx)
                try:
                    ctx.io.request(cmd.enable("force", FORCE_SPECIAL), deadline=4.0)
                except ValueError:
                    pass

        # 识字回路：刷 literate → int 永久增益（未封顶时优先）
        st = 0
        if not ctx.char.get("literate_capped"):
            st = study_literate(ctx, rounds=20)
            if st:
                ctx.log("维护", f"study 识字 {st} 次", Colors.GREEN)

        # 气→内力转化（exert recover 闭环源头；20×30气 ≈ +force 数十点）
        ex = exercise_force(ctx, rounds=15)
        if ex:
            ctx.log("维护", f"exercise {ex} 次（攒内力）", Colors.GREEN)

        if goto(ctx, "inn") == ARRIVED:
            ctx.io.request(cmd.save(), deadline=5.0)
        ctx.refresh_score()
        ctx.checkpoint(force=True)
        return RoutineResult(OUTCOME_COMPLETED,
                             f"维护完成：学习 {learned} 次 exercise {ex} 次，food={ctx.char.get('food')},water={ctx.char.get('water')}")
