"""
例程执行节点：planner 分配的 routine:* 任务直达此节点，
同步运行例程状态机至返回，将 RoutineResult 映射回图状态。
"""
from config import Colors
from mud.routines import REGISTRY
from mud.routines.base import (
    RoutineContext, OUTCOME_COMPLETED, OUTCOME_FAILED, OUTCOME_ESCALATE,
    OUTCOME_RECONNECT, OUTCOME_STOPPED, OUTCOME_GOAL,
)
from nodes.helpers import log_colored, log_task
from state import AgentState


def routine_exec(state: AgentState) -> dict:
    task = dict(state.get("current_task", {}))
    executor = task.get("executor", "")
    name = executor.split(":", 1)[1] if ":" in executor else ""
    routine_cls = REGISTRY.get(name)

    if routine_cls is None:
        log_colored("例程", f"未知例程: {executor!r}", Colors.RED)
        task["status"] = "failed"
        task["result"] = f"未知例程 {executor}"
        return {"current_task": task, "tasks": [task]}

    log_colored("例程", f"▶ 开始 [{task.get('id')}] {name}: {task.get('description', '')[:80]}", Colors.BLUE)
    ctx = RoutineContext(state)
    result = routine_cls().run(ctx, task.get("params", {}))
    log_colored("例程", f"■ [{task.get('id')}] {name} → {result.outcome}: {result.detail[:120]}",
                Colors.GREEN if result.outcome in (OUTCOME_COMPLETED, OUTCOME_GOAL) else Colors.YELLOW)
    log_task(task.get("id", "?"), "ROUTINE_RESULT", f"{result.outcome}: {result.detail}")

    updates = dict(result.state_updates)
    updates["char_status"] = ctx.char
    updates["counters"] = ctx.counters
    updates["exp_history"] = ctx.exp_history

    if result.outcome == OUTCOME_COMPLETED:
        task["status"] = "completed"
        task["result"] = result.detail
    elif result.outcome == OUTCOME_FAILED:
        task["status"] = "failed"
        task["result"] = result.detail
    elif result.outcome == OUTCOME_ESCALATE:
        task["status"] = "stuck"
        task["result"] = result.detail
    elif result.outcome == OUTCOME_RECONNECT:
        task["status"] = "in_progress"  # 重连后由 milestones 重新生成等价任务
        updates["should_reconnect"] = True
        updates["exit_reason"] = "reconnect"
    elif result.outcome == OUTCOME_STOPPED:
        task["status"] = "in_progress"
        updates["should_stop"] = True
        updates["exit_reason"] = "stop"
    elif result.outcome == OUTCOME_GOAL:
        task["status"] = "completed"
        task["result"] = result.detail
        updates["exit_reason"] = "goal_reached"

    updates["current_task"] = task
    updates["tasks"] = [task]
    return updates
