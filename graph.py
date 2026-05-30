"""
LangGraph 图构建模块
将节点组装为状态图，定义控制流。

架构（知识管理并行化）：
  planner → observe → start_kb_bg → analyze → act → sync_kb → (循环或回到 planner)
  
  - start_kb_bg: 在后台线程启动 manage_knowledge，立即返回
  - analyze + act: 与知识管理并行执行
  - sync_kb: 等待后台知识管理完成，合并结果

规划者只在任务制定/推进时被调用，不参与执行循环。
"""
from langgraph.graph import StateGraph, END

from state import AgentState
from nodes import (
    observe, analyze, act,
    start_knowledge_update_bg, sync_knowledge_update,
    planner
)


def _route_after_planner(state: AgentState) -> str:
    """
    planner 之后的路由：
    - 需要退出（非文本环境）→ END
    - 已分配任务 → observe（进入执行循环）
    """
    if state.get("should_exit", False):
        return "end"
    return "observe"


def _route_after_observe(state: AgentState) -> str:
    """observe 之后的路由：连接断开→END，否则→start_kb_bg"""
    if state.get("should_reconnect", False):
        return "end"
    return "start_kb_bg"


def _route_after_act(state: AgentState) -> str:
    """act 之后的路由：连接断开→END，否则→sync_kb"""
    if state.get("should_reconnect", False):
        return "end"
    return "sync_kb"


def _route_after_analyze(state: AgentState) -> str:
    """
    analyze 之后的路由：
    - 当前任务已完成/陷入僵局 → 不再执行动作，直接同步知识库后回 planner
    - 否则 → act 执行模型选择的动作
    """
    if state.get("task_completed", False):
        return "sync_kb"
    if state.get("task_stuck", False):
        return "sync_kb"
    return "act"


def _route_after_sync_kb(state: AgentState) -> str:
    """
    sync_kb 之后的路由：
    - 当前任务已完成 → planner（回到规划者拿下一个任务）
    - 当前任务陷入僵局 → planner（回到规划者重新决策）
    - 当前任务未完成 → observe（继续执行循环）
    """
    if state.get("task_completed", False):
        return "planner"
    if state.get("task_stuck", False):
        return "planner"
    return "observe"


def build_graph():
    """
    构建并编译 LangGraph 状态图。
    
    架构（知识管理并行化）：
        planner → observe → start_kb_bg → analyze → act → sync_kb
                    ↑                                       ↓
                    └──── 任务未完成 ────────────────────────┘
                                                            ↓
                    planner ←── 任务已完成 ──────────────────┘
    
    - planner: 制定任务+计划（独立于循环，只在任务切换时调用）
    - observe: 观察服务器输出
    - start_kb_bg: 在后台线程启动知识管理，立即返回
    - analyze: 分析并决策（与后台知识管理并行）
    - act: 执行行动（与后台知识管理并行）
    - sync_kb: 等待后台知识管理完成，同步知识库
    
    返回编译后的 CompiledGraph。
    """
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("planner", planner)
    graph.add_node("observe", observe)
    graph.add_node("start_kb_bg", start_knowledge_update_bg)
    graph.add_node("analyze", analyze)
    graph.add_node("act", act)
    graph.add_node("sync_kb", sync_knowledge_update)

    # 入口：规划者先制定任务
    graph.set_entry_point("planner")

    # planner → observe 或 END
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "observe": "observe",
            "end": END,
        },
    )

    # observe → start_kb_bg 或 END
    graph.add_conditional_edges(
        "observe",
        _route_after_observe,
        {
            "start_kb_bg": "start_kb_bg",
            "end": END,
        },
    )

    # start_kb_bg → analyze（后台知识管理已启动，立即进入分析）
    graph.add_edge("start_kb_bg", "analyze")

    # analyze → act 或 sync_kb
    graph.add_conditional_edges(
        "analyze",
        _route_after_analyze,
        {
            "act": "act",
            "sync_kb": "sync_kb",
        },
    )

    # act → sync_kb 或 END
    graph.add_conditional_edges(
        "act",
        _route_after_act,
        {
            "sync_kb": "sync_kb",
            "end": END,
        },
    )

    # sync_kb → observe（继续循环）或 planner（任务完成）
    graph.add_conditional_edges(
        "sync_kb",
        _route_after_sync_kb,
        {
            "observe": "observe",
            "planner": "planner",
        },
    )

    return graph.compile()
