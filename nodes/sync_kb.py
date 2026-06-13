"""
知识更新同步模块
等待后台知识管理线程完成并合并结果。
"""
from concurrent.futures import Future

from config import Colors
from state import AgentState
from nodes.helpers import log_colored


def sync_knowledge_update(state: AgentState) -> dict:
    """
    同步知识管理结果节点。

    在 act 之后调用，等待后台知识管理线程完成，
    将更新后的知识库合并到 state 中。
    """
    future = state.get("kb_update_future")

    if future is None or not isinstance(future, Future):
        log_colored("知识管理", "无后台知识更新任务", Colors.RESET)
        return {"kb_update_future": None}

    try:
        result = future.result(timeout=30)  # 最多等待 30 秒
        kb = result.get("knowledge_base", state.get("knowledge_base", []))
        counter = result.get("kb_consolidation_counter", state.get("kb_consolidation_counter", 0))
        added_count = result.get("added_count", 0)

        if added_count > 0:
            log_colored("知识管理", f"后台知识更新已同步（新增 {added_count} 条）", Colors.MAGENTA)

        return {
            "knowledge_base": kb,
            "kb_consolidation_counter": counter,
            "kb_update_future": None,
        }
    except Exception as e:
        import traceback
        log_colored("知识管理",
                    f"后台知识更新失败: {type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}",
                    Colors.RED)
        return {"kb_update_future": None}
