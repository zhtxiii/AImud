"""
知识管理节点模块
按阶段管理知识库，从交互历史中提取有价值的信息。
"""
import config
from config import Colors
from state import AgentState
from nodes.helpers import (
    log_colored, log_knowledge,
    save_kb, load_all_previous_kb, get_aggregated_kb,
)


def manage_knowledge(state: AgentState) -> dict:
    """
    知识管理节点：act 之后执行。

    功能：
    1. 按阶段管理独立的知识库
    2. 区分信息类别：input_triggered（输入导致的输出）vs spontaneous（自发输出）
    3. 定期整理知识库（合并重复、更新过时、按类别归类）
    4. 根据当前阶段任务分析知识库建设重点
    """
    llm = state["llm"]
    history = state.get("history", [])
    knowledge_base = list(state.get("knowledge_base", []))  # 拷贝
    phase = state.get("phase", 1)
    phase_name = state.get("phase_name", "未知")
    tasks = state.get("tasks", [])
    counter = state.get("kb_consolidation_counter", 0)
    server_output_clean = state.get("server_output_clean", "")

    if not history and not tasks:
        return {"knowledge_base": knowledge_base, "kb_consolidation_counter": counter}

    # 构建当前阶段任务摘要
    tasks_str = ""
    for t in tasks:
        tasks_str += f"- [{t.get('id', '?')}] {t.get('description', '')[:80]} (状态: {t.get('status', '?')})\n"

    # 加载以前阶段的知识库作为参考（全量上下文）
    full_kb = get_aggregated_kb(phase, knowledge_base)
    # 以前阶段（仅用于prompt展示区分）
    prev_kb = load_all_previous_kb(phase)
    prev_kb_str = ""
    if prev_kb:
        for entry in prev_kb[-15:]:
            prev_kb_str += f"- [阶段{entry.get('from_phase', '?')}][{entry.get('category', '?')}] {entry.get('content', '')}\n"
    else:
        prev_kb_str = "无以前阶段的知识。"

    # 构建当前知识库字符串
    kb_str = ""
    if knowledge_base:
        for entry in knowledge_base:
            if isinstance(entry, dict):
                kb_str += f"- [{entry.get('category', '?')}] {entry.get('content', '')}\n"
            else:
                kb_str += f"- {entry}\n"
    else:
        kb_str = "暂无。"

    # 构建交互历史字符串
    recent_history = history[-config.MAX_HISTORY_ROUNDS:]
    history_str = "\n".join([f"{i+1}. {h}" for i, h in enumerate(recent_history)])

    system_prompt = f"""\
你是一个知识库管理员。你的职责是为当前阶段管理专门的知识库。

当前阶段: {phase} - {phase_name}

当前阶段的任务:
{tasks_str}

以前阶段的知识库（参考）:
{prev_kb_str}

当前阶段知识库:
{kb_str}

最近的交互历史:
{history_str}

服务器最新输出:
"{server_output_clean}"

你的任务：
1. 根据当前阶段的任务，分析知识库建设的重点方向,从而确定新信息的类别。
2. 从交互历史中提取有价值的新信息，更新到知识库中,额外列出新信息中出现的与当前阶段任务相关的关键词。
3. 每条知识必须标注类别 category：
   - "input_triggered": 这条信息是我们发送命令后，服务器响应中包含的信息
   - "spontaneous": 这条信息是没有我们输入也会产生的输出（如欢迎信息、系统广播、定时消息）
4. 新信息中出现的关键词必须与当前阶段任务相关。
5. 类别必须是当前阶段任务相关的具体类型。
6. 已存在于知识库中的重复信息不要再次添加。
7. 无意义的系统噪音不要记录。

严格以 JSON 格式输出：
{{
    "kb_focus": "当前阶段知识库建设的重点方向",
    "reasoning": "你的分析思路...",
    "new_entries": [
        {{"content": "知识内容...", "category": "input_triggered 或 spontaneous",
        "keywords": ["关键词1", "关键词2", ...], "类别": "具体类型"}}
    ],

}}

如果没有需要添加的新知识，new_entries 应为空列表 []。
"""

    user_msg = "请审查交互历史并更新当前阶段的知识库。"

    def kb_validator(res):
        return isinstance(res, dict) and "new_entries" in res and isinstance(res.get("new_entries"), list)

    result = llm.call_with_retry(
        system_prompt, user_msg,
        json_mode=True,
        validator=kb_validator,
        caller_id=f"KnowledgeManager[Phase{phase}]"
    )

    kb_focus = result.get("kb_focus", "")
    new_entries = result.get("new_entries", [])
    reasoning = result.get("reasoning", "")

    if kb_focus:
        log_knowledge("FOCUS", kb_focus)

    if reasoning:
        log_knowledge("REASONING", reasoning)

    added_count = 0
    for entry in new_entries:
        if not entry or not isinstance(entry, dict):
            continue
        content = entry.get("content", "")
        category = entry.get("category", "unknown")
        if not content:
            continue
        # 检查重复
        is_dup = any(
            e.get("content") == content
            for e in knowledge_base
            if isinstance(e, dict)
        )
        if is_dup:
            log_knowledge("DUPLICATE", f"跳过重复: {content}")
            continue

        new_entry = {
            "content": content,
            "category": category,
            "keywords": entry.get("keywords", []),
            "specific_type": entry.get("类别", "unknown")
        }
        knowledge_base.append(new_entry)
        log_knowledge("ADD", f"[{category}] {content} (Tags: {new_entry['keywords']}, Type: {new_entry['specific_type']})")
        added_count += 1

    counter += 1

    if added_count > 0:
        save_kb(knowledge_base, phase=phase)
        log_knowledge("PERSIST", f"共新增 {added_count} 条知识，已持久化。")
    else:
        log_knowledge("INFO", "无需更新知识库。")

    # ------------------------------------------------------------------
    # 定期整理知识库
    # ------------------------------------------------------------------
    if counter >= config.KB_CONSOLIDATION_INTERVAL:
        log_colored("知识管理", "开始定期整理知识库...", Colors.MAGENTA)
        knowledge_base = _consolidate_knowledge(llm, knowledge_base, phase, phase_name)
        save_kb(knowledge_base, phase=phase)
        counter = 0
        log_colored("知识管理", "知识库整理完成。", Colors.MAGENTA)

    return {
        "knowledge_base": knowledge_base,
        "kb_consolidation_counter": counter,
        "added_count": added_count,
    }


def _consolidate_knowledge(llm, knowledge_base, phase, phase_name):
    """
    整理知识库：合并重复、更新过时信息、按类别归类。
    """
    if not knowledge_base:
        return knowledge_base

    kb_str = ""
    for i, entry in enumerate(knowledge_base):
        if isinstance(entry, dict):
            kb_str += f"{i+1}. [{entry.get('category', '?')}] {entry.get('content', '')}\n"
        else:
            kb_str += f"{i+1}. {entry}\n"

    system_prompt = f"""\
你是一个知识库整理专家。请整理以下知识库，执行以下操作：

1. 合并含义重复或相似的条目。
2. 将过时的信息标记为过时或删除。
3. 确保每条知识正确标注了类别 (input_triggered 或 spontaneous)。
4. 保持知识的准确性和简洁性。

当前阶段: {phase} - {phase_name}

当前知识库:
{kb_str}

严格以 JSON 格式输出：
{{
    "reasoning": "整理思路...",
    "consolidated_entries": [
        {{"content": "整理后的知识...", "category": "...", "keywords": [...], "specific_type": "..."}}
    ]
}}
"""
    def validator(res):
        return isinstance(res, dict) and "consolidated_entries" in res

    result = llm.call_with_retry(
        system_prompt, "请整理知识库。",
        json_mode=True, validator=validator,
        caller_id=f"KB-Consolidate[Phase{phase}]"
    )

    entries = result.get("consolidated_entries", [])
    if entries:
        valid_entries = []
        for e in entries:
             if isinstance(e, dict) and e.get("content"):
                 valid_entries.append({
                     "content": e.get("content"),
                     "category": e.get("category", "unknown"),
                     "keywords": e.get("keywords", []),
                     "specific_type": e.get("specific_type", "unknown")
                 })
        if valid_entries:
            log_colored("知识管理", f"整理后知识库: {len(knowledge_base)} -> {len(valid_entries)} 条", Colors.MAGENTA)
            log_knowledge("CONSOLIDATE", f"整理后知识库: {len(knowledge_base)} -> {len(valid_entries)} 条")
            return valid_entries

    return knowledge_base
