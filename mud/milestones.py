"""
里程碑驱动的任务骨架（grind 模式）。
代码写死策略骨架，LLM 只处理例程升级上来的异常。

M0 立足：登录/建号 → bootstrap（捡钱/买食水/吃喝/wimpy/拜师/学基础技能/save）
M1 雪镇切磋：trainee→李火狮 刷到 2000（顺带速率标定数据）
M2 云镇双循环：quest ⨉ spar 交替 + 周期性 maintain，直到 GOAL_EXP
M3 验收：score+save+存档双重验证
"""
import config

_task_seq = 0


def _task(executor: str, description: str, params: dict = None) -> dict:
    global _task_seq
    _task_seq += 1
    return {
        "id": f"G-{_task_seq}",
        "description": description,
        "status": "in_progress",
        "result": None,
        "executor": executor,
        "params": params or {},
    }


def milestone_of(exp: int, char: dict) -> str:
    if not char.get("logged_in"):
        return "M0"
    if not char.get("bootstrap_done"):
        return "M0"
    if exp < config.GOAL_EXP:
        return "M1"
    if exp < config.GOAL_EXP:
        return "M2"
    return "M3"


def next_task(state: dict) -> dict:
    """根据当前状态返回下一个任务定义（带 executor/params）。"""
    char = state.get("char_status", {})
    exp = char.get("exp", 0)
    ms = state.setdefault("milestone", {})

    # 登录优先于一切（死亡踢线后必须先回到游戏内才能复活）
    if not char.get("logged_in"):
        return _task("routine:login", "登录/创建角色，处理接管提示，进入游戏")

    # 死亡优先：复活例程
    if char.get("ghost"):
        return _task("routine:death_recovery", "角色已死亡，执行复活流程并恢复状态")

    mid = milestone_of(exp, char)
    if ms.get("id") != mid:
        ms.clear()
        ms["id"] = mid
        ms["cycle"] = 0

    if mid == "M0":
        if not char.get("logged_in"):
            return _task("routine:login", "登录/创建角色，处理接管提示，进入游戏")
        return _task("routine:bootstrap",
                     "开局立足：捡钱→买食水→进食→set wimpy→拜师柳淳风→学习基础技能→save")

    if mid == "M1":
        return _task("routine:spar",
                     f"修炼傀儡加速训练至 {config.GOAL_EXP}（当前 {exp}）",
                     {"target_exp": config.GOAL_EXP, "budget_min": 60})

    if mid == "M2":
        cycle = ms.get("cycle", 0)
        ms["cycle"] = cycle + 1
        # 每 6 个周期做一次全面补给维护
        if cycle > 0 and cycle % 6 == 0:
            return _task("routine:maintain", "周期性维护：补食水/疗伤/学习技能/save",
                         {"full": True})
        # 经验刚过门槛时先确保稳定 >1100 再进任务循环
        if exp < 1100:
            return _task("routine:spar",
                         f"切磋至 1100 以稳定解锁任务系统（当前 {exp}）",
                         {"target_exp": 1100, "budget_min": 30})
        if cycle % 2 == 0:
            return _task("routine:quest",
                         f"任务循环（当前经验 {exp}）：领单→评估→击杀→回报",
                         {"budget_min": 25})
        return _task("routine:spar",
                     f"切磋循环（当前经验 {exp}）：阶梯目标刷经验",
                     {"budget_min": 25})

    # M3
    return _task("routine:verify",
                 f"最终验收：save + score 解析 + 存档双重验证 经验 ≥ {config.GOAL_EXP}")
