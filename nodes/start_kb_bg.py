"""
后台知识更新启动模块
在 observe 之后立即将知识管理提交到后台线程池。

整流策略：
- routine:* 任务期间完全旁路（例程不产生需要 LLM 提炼的知识，且避免线程堆积）
- skip-if-busy：上一个后台任务未完成时跳过本轮，杜绝队列堆积导致的分钟级挂起
"""
from state import AgentState
from nodes.helpers import _kb_executor, log_colored
from nodes.manage_knowledge import manage_knowledge

_pending_future = None


def _run_knowledge_update_in_bg(state_snapshot: dict) -> dict:
    """
    在后台线程中执行知识管理逻辑。
    接收 state 的快照（纯数据），返回知识库更新结果。
    """
    result = manage_knowledge(state_snapshot)
    return result


def start_knowledge_update_bg(state: AgentState) -> dict:
    """
    后台启动知识管理节点。

    在 observe 之后立即调用，将 manage_knowledge 提交到后台线程池，
    然后立即返回，不阻塞 analyze 和 act 的执行。
    """
    global _pending_future

    # routine 任务期间旁路
    executor = state.get("current_task", {}).get("executor", "")
    if executor.startswith("routine:"):
        return {"kb_update_future": None}

    # skip-if-busy：上一轮还没完成就不再提交
    if _pending_future is not None and not _pending_future.done():
        log_colored("知识管理", "上一轮知识更新未完成，本轮跳过（skip-if-busy）")
        return {"kb_update_future": None}

    # 创建 state 快照（只包含 manage_knowledge 需要的字段）
    state_snapshot = {
        "llm": state["llm"],
        "history": list(state.get("history", [])),
        "knowledge_base": list(state.get("knowledge_base", [])),
        "phase": state.get("phase", 1),
        "phase_name": state.get("phase_name", "未知"),
        "tasks": list(state.get("tasks", [])),
        "kb_consolidation_counter": state.get("kb_consolidation_counter", 0),
        "server_output_clean": state.get("server_output_clean", ""),
    }

    future = _kb_executor.submit(_run_knowledge_update_in_bg, state_snapshot)
    _pending_future = future

    return {"kb_update_future": future}
