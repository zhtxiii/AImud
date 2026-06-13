"""
LangGraph 状态定义模块
定义智能体在图节点间传递的状态结构。
"""
from typing import Any
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """
    智能体状态，在 LangGraph 节点间传递。
    
    使用 total=False 允许部分更新（节点只需返回修改的字段）。
    """
    # --- 持久实例 ---
    client: Any              # SocketClient 实例
    llm: Any                 # LLMClient 实例

    # --- 当前轮数据 ---
    server_output: str       # 服务器原始输出（含 ANSI）
    server_output_clean: str # 清洗后的纯文本输出

    # --- 记忆 ---
    history: list[str]       # 短期交互历史
    knowledge_base: list[dict]  # 当前阶段知识库（每条含 content, category 字段）

    # --- 阶段与任务（规划者驱动） ---
    phase: int               # 当前阶段编号（从1开始）
    phase_name: str          # 当前阶段名称
    tasks: list[dict]        # 当前阶段任务列表 [{id, description, status, result}]
    current_task: dict       # 当前正在执行的任务（由规划者分配）
    completed_phases: list[dict]  # 已完成阶段摘要 [{phase, name, tasks_summary, key_findings}]
    environment_type: str    # 识别出的环境类型（mud/shell/chat/llm_qa/unknown）

    # --- LLM 决策结果 ---
    analysis: str            # LLM 分析文本
    action_type: str         # 动作类型：send / enter / wait
    payload: str             # 要发送的 Payload
    expected_result: str     # 预期服务器响应
    last_client_payload: str # 上一次实际发送给服务器的内容

    # --- 控制流 ---
    should_reconnect: bool   # 需要重连（连接断开）
    should_stop: bool        # 需要停止（用户中断）
    should_exit: bool        # 非文本环境，需要退出
    task_completed: bool     # 当前任务已完成（由 analyze 判定，触发返回 planner）
    task_stuck: bool         # 当前任务陷入僵局（由 analyze 判定，触发返回 planner）
    task_attempts: int       # 当前任务已执行的循环次数
    task_stuck_reason: str   # 僵局原因描述（含部分成果）

    # --- 知识库管理 ---
    kb_consolidation_counter: int  # 知识库整理计数器
    kb_update_future: Any          # 后台知识管理线程的 Future 对象

    # --- 反思与经验 ---
    experiences: list[dict]        # 反思后积累的经验列表
    skills: list[dict]             # 反思后积累的技能列表

    # --- grind 模式（10万经验任务） ---
    char_status: dict        # 角色状态 {id,name,exp,potential,gin/kee/sen(+max),food,water,
                             #  force,money,location_node,skills:{},wounded,family,updated_at}
    milestone: dict          # 当前里程碑 {id, params, started_at}
    exp_history: list        # [[ts, exp], ...] 截尾保留最近500点
    credentials: dict        # 角色凭据（运行时引用，持久化在 credentials.json）
    escalation: dict         # 例程升级上下文 {routine, reason, detail, room, recent_output, attempts}
    exit_reason: str         # none/reconnect/stop/fatal/goal_reached（统一退出语义）
    counters: dict           # {deaths, quests_done, quests_skipped, reconnects, llm_failures}
