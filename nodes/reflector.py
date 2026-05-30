"""
反思者模块
负责分析已完成的任务，生成经验和技能，并管理其持久化存储。
"""
import os
import json
import time
import datetime
from typing import List, Dict, Any

import config
from config import Colors
from nodes.helpers import log_colored

def _log_reflector(message: str, color: str = None):
    """反思者专属日志"""
    log_colored("反思者", message, color)

    # 写入文件
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}\n"
    try:
        os.makedirs(os.path.dirname(config.REFLECTOR_LOG_FILE), exist_ok=True)
        with open(config.REFLECTOR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted_msg)
    except Exception as e:
        print(f"写入反思者日志失败: {e}")

def _load_experiences() -> Dict[str, List[Dict]]:
    """从文件加载经验和技能"""
    if not os.path.exists(config.EXPERIENCES_FILE):
        return {"experiences": [], "skills": []}

    try:
        with open(config.EXPERIENCES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log_reflector(f"加载经验失败: {e}", Colors.RED)
        return {"experiences": [], "skills": []}

def _save_experiences(data: Dict[str, List[Dict]]):
    """保存经验和技能到文件"""
    try:
        os.makedirs(config.REFLECTIONS_DIR, exist_ok=True)
        with open(config.EXPERIENCES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log_reflector(f"保存经验失败: {e}", Colors.RED)

def reflect_on_task(llm: Any, task: Dict, knowledge_base: List[Dict], phase: int) -> Dict[str, List[Dict]]:
    """
    对已完成（或陷入僵局）的任务进行反思。

    参数：
        llm: LLM 客户端
        task: 任务对象（包含 id、description、result、status）
        knowledge_base: 当前知识库
        phase: 当前阶段编号

    返回：
        包含 new_experiences 和 new_skills 的字典
    """
    task_id = task.get("id", "unknown")
    task_status = task.get("status", "unknown")

    _log_reflector(f"开始反思任务 [{task_id}]（状态: {task_status}）", Colors.MAGENTA)

    # 1. 读取任务日志
    log_path = os.path.join(config.TASK_LOG_DIR, f"{task_id}.log")
    if not os.path.exists(log_path):
        _log_reflector(f"任务日志未找到: {log_path}", Colors.RED)
        return {"new_experiences": [], "new_skills": []}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            task_log_content = f.read()
    except Exception as e:
        _log_reflector(f"读取任务日志失败: {e}", Colors.RED)
        return {"new_experiences": [], "new_skills": []}

    # 2. 加载已有知识
    existing_data = _load_experiences()
    current_experiences = existing_data.get("experiences", [])
    current_skills = existing_data.get("skills", [])

    # 3. 构建提示词
    existing_exp_str = json.dumps(current_experiences[-5:], indent=2, ensure_ascii=False) if current_experiences else "无"
    existing_skills_str = json.dumps([s["name"] for s in current_skills], indent=2, ensure_ascii=False) if current_skills else "无"

    system_prompt = f"""
你是一个高级 AI 反思者。你的目标是分析一个自主智能体在文本交互环境（MUD）中执行任务的日志，从中提炼有价值的经验和可复用的技能。

任务 ID: {task_id}
任务描述: {task.get("description")}
最终状态: {task_status}
结果/僵局原因: {task.get("result", "无")}

已有技能: {existing_skills_str}

你的分析应该聚焦于：
1. **经验（通用教训）**：哪些做法有效？哪些做法失败了？发现了哪些关于环境或命令的通用使用模式？（例如："look 命令可以显示出口"、"名为 Guard 的 NPC 会挡路"）。
2. **技能（可复用流程）**：识别出达成某个子目标的具体、可重复的操作序列。一个技能必须有明确的触发条件和执行步骤。（例如："技能：检查背包"、"技能：导航到城镇广场"）。

输入 - 任务执行日志：
{task_log_content[-8000:]}
（日志过长时已截断）

输出要求：
严格以 JSON 格式输出：
{{
    "new_experiences": [
        {{
            "summary": "一句话总结",
            "lesson": "详细的经验教训",
            "tags": ["标签1", "标签2"]
        }}
    ],
    "new_skills": [
        {{
            "name": "技能名称",
            "description": "该技能的作用",
            "trigger": "何时应该使用该技能（上下文/条件）",
            "steps": ["步骤1", "步骤2", "步骤3"],
            "expected_outcome": "执行后的预期结果",
            "tags": ["标签1"]
        }}
    ]
}}

如果没有发现有价值的经验或新技能，返回空列表。不要重复已有技能，除非你有显著的改进。
    """

    # 4. 调用 LLM
    try:
        response = llm.call_with_retry(
            system_prompt,
            "请分析日志并生成经验和技能。",
            json_mode=True,
            think=True,
            caller_id="反思者"
        )
    except Exception as e:
        _log_reflector(f"LLM 调用失败: {e}", Colors.RED)
        return {"new_experiences": [], "new_skills": []}

    new_experiences = response.get("new_experiences", [])
    new_skills = response.get("new_skills", [])

    # 5. 处理并保存
    timestamp = datetime.datetime.now().isoformat()

    # 补充经验元数据
    for exp in new_experiences:
        exp["id"] = f"EXP-{int(time.time())}-{len(existing_data.get('experiences', []))}"
        exp["task_id"] = task_id
        exp["phase"] = phase
        exp["created_at"] = timestamp
        existing_data.setdefault("experiences", []).append(exp)
        _log_reflector(f"生成经验: {exp['summary']}", Colors.GREEN)

    # 补充技能元数据
    for skill in new_skills:
        skill["id"] = f"SKILL-{int(time.time())}-{len(existing_data.get('skills', []))}"
        skill["source_task"] = task_id
        skill["created_at"] = timestamp
        existing_data.setdefault("skills", []).append(skill)
        _log_reflector(f"生成技能: {skill['name']}", Colors.GREEN)

    if new_experiences or new_skills:
        _save_experiences(existing_data)

    return {"new_experiences": new_experiences, "new_skills": new_skills}
