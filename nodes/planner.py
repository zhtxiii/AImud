"""
规划者节点模块
独立于执行循环之外，只负责：制定阶段任务、分配当前任务、推进阶段。
不参与 observe/analyze/act/manage_knowledge 循环。

grind 模式：里程碑驱动（mud/milestones.py 静态策略骨架），
LLM 只处理例程升级上来的异常（修复任务）。
"""
import json
import os
import time
import config
import persistence
from config import Colors
from state import AgentState


import datetime

from nodes.helpers import log_colored, get_aggregated_kb
from nodes.reflector import reflect_on_task


def _log(tag: str, message: str, color: str = None):
    """复用日志函数"""
    log_colored(tag, message, color)


def _log_planner_event(event_type: str, message: str):
    """
    记录 Planner 专属日志
    格式: [时间] [事件类型] 消息
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{event_type}] {message}\n"

    try:
        with open(config.PLANNER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        _log("PlannerLog", f"写入日志失败: {e}", Colors.RED)


# ============================================================
#  grind 模式规划者（里程碑驱动）
# ============================================================

def _build_repair_task(escalation: dict) -> dict:
    """根据升级上下文生成 LLM 修复任务。"""
    desc = (
        f"例程 [{escalation.get('routine')}] 遇到异常需要你修复。\n"
        f"异常原因: {escalation.get('reason')} - {escalation.get('detail')}\n"
        f"当前位置(推测): {escalation.get('room_label')} ({escalation.get('room')})\n"
        f"角色状态: {json.dumps(escalation.get('char_status', {}), ensure_ascii=False)}\n"
        f"最近服务器输出片段:\n{escalation.get('recent_output', '')[-800:]}\n\n"
        f"你的目标：把角色恢复到安全、稳定、可继续的状态——"
        f"具体包括：(1) 不在战斗中且没有持续掉血；(2) 用 look 确认身处一个正常房间；"
        f"(3) 若迷路，向已知方向移动回到雪亭镇或云镇的可识别地点；"
        f"(4) 若有异常提示符（分页/问答），用合适输入清除它。"
        f"达成后将 task_completed 设为 true 并在 task_result 里描述当前位置与状态。"
        f"注意：绝不要输入 quit/suicide 等危险命令，绝不要攻击任何 NPC。"
        f"若看到登录界面提示（您的英文名字/请输入密码），不要输入任何名字或密码——"
        f"立即设置 task_stuck=true 并说明'连接处于登录界面'，登录由系统例程负责。"
    )
    return {
        "id": f"R-{int(time.time()) % 100000}",
        "description": desc,
        "status": "in_progress",
        "result": None,
        "executor": "llm",
        "max_attempts": config.MAX_REPAIR_ATTEMPTS,
        "params": {},
        "plan": "评估现状→清除异常状态→回到安全已知位置→确认稳定",
    }


def _grind_planner(state: AgentState) -> dict:
    """里程碑驱动规划。每次进入先存 checkpoint。"""
    from mud import milestones

    persistence.save_checkpoint(state)
    char = state.get("char_status", {})
    counters = state.setdefault("counters", {})
    task = state.get("current_task") or {}
    updates = {"task_completed": False, "task_stuck": False}

    # ---- 修复任务（LLM）结束：反思 + 清除升级上下文 ----
    if task.get("executor") == "llm" and task.get("id", "").startswith("R-"):
        if state.get("task_completed") or state.get("task_stuck"):
            status = "完成" if state.get("task_completed") else "僵局"
            _log("规划者", f"修复任务 [{task.get('id')}] {status}: {str(task.get('result'))[:100]}", Colors.YELLOW)
            _log_planner_event("REPAIR_DONE", f"[{task.get('id')}] {status}")
            try:
                full_kb = get_aggregated_kb(state.get("phase", 1), state.get("knowledge_base", []))
                reflections = reflect_on_task(state["llm"], task, full_kb, state.get("phase", 1))
                new_exp = reflections.get("new_experiences", [])
                if new_exp:
                    state.setdefault("experiences", []).extend(new_exp)
            except Exception as e:
                _log("规划者", f"修复任务反思失败（忽略）: {e}", Colors.YELLOW)
            updates["escalation"] = {}
            if state.get("task_stuck"):
                counters["repair_failures"] = counters.get("repair_failures", 0) + 1
                if counters["repair_failures"] >= 3:
                    _log("规划者", "修复任务连续失败 3 次，置 fatal 交 watchdog。", Colors.RED)
                    persistence.save_checkpoint(state)
                    return {**updates, "exit_reason": "fatal", "escalation": {}}
            else:
                counters["repair_failures"] = 0

    # ---- 例程升级 → 生成修复任务 ----
    esc = state.get("escalation") or {}
    if esc and not esc.get("repair_dispatched"):
        key = f"esc_{esc.get('routine')}:{esc.get('reason')}"
        cnt = counters.get(key, 0) + 1
        counters[key] = cnt
        if cnt > 3:
            _log("规划者", f"同一升级原因 {key} 已出现 {cnt} 次，置 fatal。", Colors.RED)
            persistence.save_checkpoint(state)
            return {**updates, "exit_reason": "fatal"}
        repair = _build_repair_task(esc)
        esc = dict(esc)
        esc["repair_dispatched"] = True
        _log("规划者", f"派发修复任务 [{repair['id']}]（{esc.get('routine')}/{esc.get('reason')} 第 {cnt} 次）", Colors.BLUE)
        _log_planner_event("REPAIR_DISPATCH", f"[{repair['id']}] {esc.get('reason')}")
        return {**updates, "current_task": repair, "tasks": [repair], "escalation": esc}

    # ---- 例程任务正常完成：清零该例程的升级计数 ----
    if task.get("executor", "").startswith("routine:") and task.get("status") == "completed":
        rname = task["executor"].split(":", 1)[1]
        for k in list(counters.keys()):
            if k.startswith(f"esc_{rname}:"):
                counters[k] = 0

    # ---- 最终验收完成 → goal_reached ----
    if task.get("executor") == "routine:verify" and task.get("status") == "completed":
        _log("规划者", f"🎉 验收通过：{task.get('result')}", Colors.GREEN)
        _log_planner_event("GOAL_REACHED", str(task.get("result")))
        persistence.save_checkpoint(state)
        return {**updates, "exit_reason": "goal_reached"}

    # ---- 硬停滞检测（exp>0 且超过 STALL_HARD_MIN 无增长） ----
    hist = state.get("exp_history") or []
    if hist and hist[-1][1] > 0:
        idle_min = (time.time() - hist[-1][0]) / 60
        if idle_min > config.STALL_HARD_MIN:
            _log("规划者", f"经验已 {idle_min:.0f} 分钟无增长（硬停滞），置 fatal。", Colors.RED)
            persistence.save_checkpoint(state)
            return {**updates, "exit_reason": "fatal"}

    # ---- 里程碑推进 ----
    nt = milestones.next_task(state)
    ms_id = state.get("milestone", {}).get("id", "?")
    _log("规划者", f"=== 里程碑 {ms_id} | 经验 {char.get('exp', 0)}/{config.GOAL_EXP} ===", Colors.BLUE)
    _log("规划者", f"分配任务 [{nt['id']}] {nt['executor']}: {nt['description'][:70]}", Colors.BLUE)
    _log_planner_event("TASK_ASSIGNED", f"[{nt['id']}] {nt['executor']} {nt['description'][:80]}")
    return {**updates, "current_task": nt, "tasks": [nt],
            "milestone": state.get("milestone", {})}


# ============================================================
#  第一阶段固定任务
# ============================================================

PHASE1_TASKS = [
    {
        "id": "P1-T1",
        "description": "观察服务器的初始输出，判断这个socket连接是否基于文本的交互环境。"
                       "如果收到二进制数据或无法解码的内容，则判定为非文本环境。",
        "status": "pending",
        "result": None,
    },
    {
        "id": "P1-T2",
        "description": "如果确认是文本环境，进一步分析这是什么类型的交互环境。"
                       "可能的类型包括：文字MUD游戏、聊天系统、Linux Shell、"
                       "大模型问答接口、BBS论坛、或其他类型。"
                       "根据文本的格式、提示符、欢迎信息等特征进行判断。",
        "status": "pending",
        "result": None,
    },
]


# ============================================================
#  规划者节点
# ============================================================

def planner(state: AgentState) -> dict:
    """
    规划者节点：独立于执行循环之外的调度中心。

    grind 模式 → 里程碑驱动（_grind_planner）。
    explore 模式 → 原有 LLM 开放式规划：
    1. 制定当前阶段的任务列表
    2. 从任务列表选取下一个待执行任务并制定计划
    3. 在阶段任务全部完成时推进到下一阶段
    """
    if config.AGENT_MODE == "grind":
        return _grind_planner(state)

    llm = state["llm"]
    phase = state.get("phase", 1)
    phase_name = state.get("phase_name", "环境识别")
    tasks = state.get("tasks", [])
    completed_phases = list(state.get("completed_phases", []))
    knowledge_base = state.get("knowledge_base", [])
    environment_type = state.get("environment_type", "unknown")
    history = state.get("history", [])

    _log("规划者", f"=== 阶段 {phase}: {phase_name} ===", Colors.BLUE)

    # ------------------------------------------------------------------
    # 步骤1：如果没有任务列表，制定当前阶段任务
    # ------------------------------------------------------------------
    if not tasks:
        if phase == 1:
            tasks = [dict(t) for t in PHASE1_TASKS]  # 深拷贝
            _log("规划者", f"第一阶段固定任务已加载（{len(tasks)}个任务）", Colors.BLUE)
            _log_planner_event("PHASE_START", f"开始阶段 {phase}: {phase_name} (任务数: {len(tasks)})")
        else:
            # 获取全量知识用于规划
            full_kb = get_aggregated_kb(phase, knowledge_base)
            tasks = _generate_phase_tasks(
                llm, phase, completed_phases, full_kb, environment_type
            )
            _log("规划者", f"第{phase}阶段任务已生成（{len(tasks)}个任务）", Colors.BLUE)
            _log_planner_event("PHASE_START", f"开始阶段 {phase}: {state.get('phase_name', '未命名')} (任务数: {len(tasks)})")
            for t in tasks:
                 _log_planner_event("TASK_GENERATED", f"[{t['id']}] {t['description']}")

    # ------------------------------------------------------------------
    # 步骤0：反思之前的任务 (如果刚完成或失败)
    # ------------------------------------------------------------------
    if state.get("task_completed", False) or state.get("task_stuck", False):
        last_task = state.get("current_task", {})
        if last_task and last_task.get("id"):
            # 获取全量知识用于反思
            full_kb = get_aggregated_kb(phase, knowledge_base)

            # 调用反思者
            reflections = reflect_on_task(llm, last_task, full_kb, phase)

            # 更新状态中的经验和技能
            new_experiences = reflections.get("new_experiences", [])
            new_skills = reflections.get("new_skills", [])

            if new_experiences:
                state.setdefault("experiences", []).extend(new_experiences)
            if new_skills:
                state.setdefault("skills", []).extend(new_skills)

            # 重置 task_completed 标志 (task_stuck 会在后面处理)
            if state.get("task_completed", False):
                 state["task_completed"] = False

    # ------------------------------------------------------------------
    # 步骤1.5：处理 stuck 任务 (优先于阶段完成检查)
    # ------------------------------------------------------------------
    if state.get("task_stuck", False):
        stuck_reason = state.get("task_stuck_reason", "未知原因")
        current_task = state.get("current_task", {})
        task_id = current_task.get("id", "?")

        _log("规划者", f"处理僵局任务 [{task_id}]: {stuck_reason}", Colors.RED)

        # 获取全量知识用于决策
        full_kb = get_aggregated_kb(phase, knowledge_base)

        # LLM 决策如何处理
        action_updates = _handle_stuck_task(llm, current_task, stuck_reason, full_kb, phase)

        # 更新任务列表
        for t in tasks:
            if t["id"] == task_id:
                t.update(action_updates)
                _log("规划者", f"任务 [{task_id}] 更新状态为: {t['status']}", Colors.YELLOW)
                if t["status"] == "in_progress":
                     # 如果是重试，需要更新 current_task 以便后续生成新计划（或者由下文的分配逻辑处理）
                     # 这里将 status 设为 pending 让下文逻辑重新分配更稳妥？
                     # 不，_handle_stuck_task 返回的 status 可能是 'pending' (重试)
                     pass
                break

        # 更新本地变量，以便流程继续
        task_stuck = False
        state["task_stuck"] = False
        current_task = {}  # 重置当前任务，让后续逻辑重新分配

        _log("规划者", "僵局任务处理完毕，继续检查后续任务...", Colors.CYAN)
        _log_planner_event("TASK_STUCK_HANDLED", f"[{task_id}] 状态更新为: {action_updates.get('status')} | 原因: {stuck_reason}")

    # ------------------------------------------------------------------
    # 步骤2：检查是否所有任务完成 → 推进阶段
    # ------------------------------------------------------------------
    all_done = all(t["status"] in ("completed", "skipped") for t in tasks)
    if all_done and tasks:
        _log("规划者", f"阶段 {phase} 所有任务已完成，准备推进到下一阶段。", Colors.BLUE)

        # 检查是否为非文本环境需要退出
        if environment_type == "non_text":
            _log("规划者", "检测到非文本交互环境，准备退出。", Colors.RED)
            return {
                "tasks": tasks,
                "current_task": {},
                "should_exit": True,
            }

        # 保存阶段摘要
        phase_summary = {
            "phase": phase,
            "name": phase_name,
            "tasks_summary": [
                {"id": t["id"], "description": t["description"][:80], "result": t.get("result", "")}
                for t in tasks
            ],
            "key_findings": _extract_key_findings(tasks),
        }
        completed_phases.append(phase_summary)
        _log_planner_event("PHASE_COMPLETE", f"阶段 {phase} 完成。关键发现: {phase_summary['key_findings']}")

        # 推进到新阶段
        new_phase = phase + 1
        # 获取全量知识用于新阶段规划（当前阶段知识库尚未清空，加上之前的所有）
        full_kb_for_planning = get_aggregated_kb(phase, knowledge_base)

        new_phase_name = _determine_phase_name(llm, new_phase, completed_phases, full_kb_for_planning, environment_type)
        new_tasks = _generate_phase_tasks(llm, new_phase, completed_phases, full_kb_for_planning, environment_type)

        _log("规划者", f"进入阶段 {new_phase}: {new_phase_name}（{len(new_tasks)}个任务）", Colors.BLUE)
        _log_planner_event("PHASE_START", f"开始阶段 {new_phase}: {new_phase_name} (任务数: {len(new_tasks)})")
        for t in new_tasks:
             _log_planner_event("TASK_GENERATED", f"[{t['id']}] {t['description']}")

        # 选取新阶段的第一个任务
        first_task = new_tasks[0] if new_tasks else {}
        if first_task:
            # 此时 knowledge_base 即将清空，但制定计划时应使用之前的全量知识作为背景
            # 这里的 full_kb_for_planning 包含了直到上一阶段的所有知识
            skills = state.get("skills", [])
            plan = _create_execution_plan(llm, first_task, history, full_kb_for_planning, new_phase, new_phase_name, skills)
            first_task["status"] = "in_progress"
            first_task["plan"] = plan
            _log("规划者", f"分配任务 [{first_task['id']}]: {first_task['description'][:60]}...", Colors.BLUE)

        return {
            "phase": new_phase,
            "phase_name": new_phase_name,
            "tasks": new_tasks,
            "current_task": dict(first_task) if first_task else {},
            "completed_phases": completed_phases,
            "task_completed": False,
            "knowledge_base": [],  # 新阶段开始，重置当前阶段知识库
        }

    # ------------------------------------------------------------------
    # 步骤3：选取下一个待执行任务，制定执行计划
    # 优先处理因中断而卡在 in_progress 的任务，其次是 pending 的新任务
    # ------------------------------------------------------------------
    next_task = None

    # 优先：查找因连接中断而卡在 in_progress 的任务（需要重新执行）
    for t in tasks:
        if t["status"] == "in_progress":
            next_task = t
            _log("规划者", f"发现被中断的任务 [{t['id']}]，重新分配。", Colors.YELLOW)
            break

    # 其次：查找下一个 pending 任务
    if next_task is None:
        for t in tasks:
            if t["status"] == "pending":
                next_task = t
                break

    if next_task is None:
        _log("规划者", "没有可执行的任务。", Colors.YELLOW)
        return {"tasks": tasks, "current_task": {}, "task_completed": False}

    # 制定执行计划
    # 获取全量知识
    full_kb = get_aggregated_kb(phase, knowledge_base)
    skills = state.get("skills", [])
    plan = _create_execution_plan(llm, next_task, history, full_kb, phase, phase_name, skills)
    next_task["status"] = "in_progress"
    next_task["plan"] = plan

    _log("规划者", f"分配任务 [{next_task['id']}]: {next_task['description'][:60]}...", Colors.BLUE)
    _log("规划者", f"执行计划: {plan[:100]}...", Colors.CYAN)

    _log_planner_event("TASK_ASSIGNED", f"分配任务 [{next_task['id']}]")

    return {
        "tasks": tasks,
        "current_task": dict(next_task),
        "task_completed": False,
    }


# ============================================================
#  内部辅助函数
# ============================================================

def _generate_phase_tasks(llm, phase, completed_phases, knowledge_base, environment_type):
    """由 LLM 推算新阶段的任务列表"""
    phases_str = ""
    for cp in completed_phases:
        phases_str += f"\n### 阶段 {cp['phase']}: {cp['name']}\n"
        for ts in cp.get("tasks_summary", []):
            phases_str += f"- [{ts['id']}] {ts['description']}: {ts.get('result', '无')}\n"
        phases_str += f"关键发现: {cp.get('key_findings', '无')}\n"

    kb_str = _format_kb(knowledge_base)

    system_prompt = f"""\
你是一个智能规划者。你的职责是根据已完成的工作和已有知识，为新阶段制定合理的任务列表。

环境类型: {environment_type}

已完成的阶段及任务（进度总结）:
{phases_str if phases_str else '无（这是第一个需要规划的阶段）'}

当前知识库（已获取的信息）:
{kb_str}

你的任务是：
1. 总结在这个特定的交互环境中，我们已经完成了什么，取得了什么成果。
2. 分析还有什么重要的目标没有完成。
3. 基于以上分析，推断第 {phase} 阶段应该执行的进阶任务。

任务要求：
- 进阶性：不要重复已完成或已跳过的任务，要在已有基础上深入。
- 失败约束：如果历史任务结果包含“跳过”“僵局”“失败”“不足”等信息，后续任务必须绕开同一失败路径，或先设计验证/补足前置条件的任务。
- 具体性：任务应该是具体的、可执行的、可验证的。
- 数量：每个阶段 2-5 个任务为宜。

严格以 JSON 格式输出：
{{
    "phase_name": "这个阶段的名称",
    "status_summary": "我们已经完成了X，取得了Y...",
    "gap_analysis": "还有Z没做...",
    "reasoning": "因此本阶段的重点是...",
    "tasks": [
        {{"id": "P{phase}-T1", "description": "任务描述..."}},
        {{"id": "P{phase}-T2", "description": "任务描述..."}}
    ]
}}
"""

    def validator(res):
        return isinstance(res, dict) and "tasks" in res and isinstance(res["tasks"], list)

    result = llm.call_with_retry(
        system_prompt, f"请为第 {phase} 阶段制定任务。",
        json_mode=True, validator=validator, think=True,
        caller_id=f"Planner-GenerateTasks[Phase{phase}]"
    )

    tasks = []
    for t in result.get("tasks", []):
        tasks.append({
            "id": t.get("id", f"P{phase}-T?"),
            "description": t.get("description", ""),
            "status": "pending",
            "result": None,
        })
    return tasks


def _determine_phase_name(llm, phase, completed_phases, knowledge_base, environment_type):
    """由 LLM 决定新阶段的名称"""
    phases_str = ", ".join([f"阶段{cp['phase']}: {cp['name']}" for cp in completed_phases])

    system_prompt = f"""\
你是一个智能规划者。根据以下已完成的阶段，为第 {phase} 阶段命名。
环境类型: {environment_type}
已完成阶段: {phases_str if phases_str else '无'}

严格以 JSON 格式输出：
{{"phase_name": "简短的阶段名称"}}
"""
    result = llm.call_with_retry(
        system_prompt, f"请为第 {phase} 阶段命名。",
        json_mode=True, think=True,
        caller_id=f"Planner-NamePhase[Phase{phase}]"
    )
    return result.get("phase_name", f"阶段{phase}")


def _create_execution_plan(llm, task, history, knowledge_base, phase, phase_name, skills=None):
    """为具体任务制定执行计划（不依赖服务器输出，由规划者提前制定）"""
    task_id = task.get("id", "?")
    task_desc = task.get("description", "")

    skill_str = ""
    if skills:
        skill_str = "可用技能:\n"
        for s in skills:
            skill_str += f"- {s.get('name')}: {s.get('description')} (触发条件: {s.get('trigger')})\n"
    else:
        skill_str = "暂无可用技能。"

    system_prompt = f"""
你是一个 MUD 游戏智能体的规划模块。

当前阶段: {phase} - {phase_name}
任务 [{task_id}]: {task_desc}

{skill_str}

当前知识库概览:
{_format_kb(knowledge_base)}

你需要为该任务制定一个详细的执行计划。
如果任务描述模糊，请根据知识库和阶段目标进行推断。
如果有一致的技能，请优先在计划中引用技能。

请直接输出计划内容（步骤列表或一段指导性文字），不要包含 JSON 或其他格式。
"""
    result = llm.call_with_retry(
        system_prompt, f"请为任务 {task['id']} 制定执行计划。",
        json_mode=False, think=True,
        caller_id=f"Planner-Plan[Task{task.get('id', '?')}]"
    )
    return result


def _extract_key_findings(tasks):
    """从已完成任务中提取关键发现"""
    findings = []
    for t in tasks:
        if t.get("result"):
            findings.append(f"[{t['id']}] {t['result']}")
    return "; ".join(findings) if findings else "无"


def _format_kb(knowledge_base, limit=30):
    """格式化知识库为字符串"""
    if not knowledge_base:
        return "暂无。"
    kb_str = ""
    for entry in knowledge_base[-limit:]:
        if isinstance(entry, dict):
            kb_str += f"- [{entry.get('category', '?')}] {entry.get('content', '')}\n"
        else:
            kb_str += f"- {entry}\n"
    return kb_str


def _handle_stuck_task(llm, task, stuck_reason, knowledge_base, phase):
    """
    处理陷入僵局的任务。
    由 LLM 决定：
    1. skip: 跳过（非关键任务，或只能记录部分成果）
    2. retry: 修改描述后重试（改变方法）
    """
    kb_str = _format_kb(knowledge_base, limit=20)

    system_prompt = f"""
你是一个项目经理。当前阶段（{phase}）的一个任务陷入了僵局，分析节点经过多次尝试仍无法完成。
请根据情况决定如何处理该任务。

任务信息:
ID: {task.get('id')}
描述: {task.get('description')}
原计划: {task.get('plan')}

僵局原因 / 当前状态:
{stuck_reason}

相关知识库上下文:
{kb_str}

决策选项:
1. "skip": 如果该任务对当前阶段目标不是非做不可、环境显然不支持，或只能记录部分成果，选择跳过并在 result_summary 中写清楚已知信息和缺口。
2. "pending": 如果该任务非常关键，必须完成。你需要修改任务描述（简化或换个角度），将其状态重置为 pending，以便稍后重新尝试。

注意：陷入僵局的任务不能直接标记为 completed。只有 analyze 节点在观察到明确服务器证据时才能完成任务。

严格以 JSON 格式输出：
{{
    "action": "skip" | "pending",
    "reasoning": "决策理由...",
    "new_description": "如果选择 pending，请提供修改后的任务描述；否则同原描述",
    "result_summary": "如果选择 skip，请提供任务结果摘要（基于僵局原因）"
}}
"""
    result = llm.call_with_retry(
        system_prompt, "请决策如何处理僵局任务。",
        json_mode=True, think=True,
        caller_id=f"Planner-Stuck[Task{task.get('id', '?')}]"
    )

    action = result.get("action", "skip")
    new_desc = result.get("new_description", task.get("description"))
    res_summary = result.get("result_summary", stuck_reason)

    updates = {}
    if action == "skip":
        updates = {"status": "skipped", "result": f"(跳过) {res_summary}"}
    elif action == "pending":
        updates = {"status": "pending", "description": new_desc, "result": None}
    else:
        # Fallback
        updates = {"status": "skipped", "result": f"(异常跳过) {stuck_reason}"}

    return updates
