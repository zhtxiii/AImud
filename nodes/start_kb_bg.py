"""
后台知识更新启动模块
在 observe 之后立即将知识管理提交到后台线程池。
"""
from state import AgentState
from nodes.helpers import _kb_executor
from nodes.manage_knowledge import manage_knowledge


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

    return {"kb_update_future": future}
