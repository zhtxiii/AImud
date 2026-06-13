"""
辅助函数模块
提供日志输出、知识库读写等通用工具函数。
"""
import os
import time
import json
from concurrent.futures import ThreadPoolExecutor

import config
from config import Colors


# ============================================================
#  全局后台线程池（用于并行知识管理）
# ============================================================
_kb_executor = ThreadPoolExecutor(max_workers=1)


# ============================================================
#  日志函数
# ============================================================

def log_colored(tag: str, message: str, color: str = None):
    """带颜色的日志输出，同时写入文件"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if color:
        formatted_msg = f"[{timestamp}] {color}[{tag}] {message}{Colors.RESET}"
    else:
        formatted_msg = f"[{timestamp}] [{tag}] {message}"

    print(formatted_msg)

    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
    with open(config.LOG_FILE, "a", encoding="utf-8") as f:
        f.write(formatted_msg + "\n")


def log_knowledge(tag: str, message: str):
    """写知识管理专属日志"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] [{tag}] {message}\n"
    try:
        os.makedirs(os.path.dirname(config.KNOWLEDGE_LOG_FILE), exist_ok=True)
        with open(config.KNOWLEDGE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted_msg)
    except Exception as e:
        print(f"写入知识日志失败: {e}")


def log_task(task_id: str, tag: str, message: str):
    """写任务专属日志"""
    if not task_id or task_id == "?":
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    # 确保目录存在
    if not os.path.exists(config.TASK_LOG_DIR):
        os.makedirs(config.TASK_LOG_DIR, exist_ok=True)

    log_file = os.path.join(config.TASK_LOG_DIR, f"{task_id}.log")
    formatted_msg = f"[{timestamp}] [{tag}] {message}\n"

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(formatted_msg)
    except Exception as e:
        print(f"写入任务日志失败: {e}")


# ============================================================
#  知识库读写
# ============================================================

def load_kb(phase: int = None) -> list[dict]:
    """
    从文件加载知识库。
    如果指定 phase，加载对应阶段的知识库；否则加载默认知识库。
    """
    if phase is not None:
        kb_file = os.path.join(config.KB_DIR, f"knowledge_base_phase_{phase}.json")
    else:
        kb_file = config.KB_FILE

    if not os.path.exists(kb_file):
        return []
    try:
        with open(kb_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容旧格式（list[str] → list[dict]）
            result = []
            for item in data:
                if isinstance(item, str):
                    result.append({"content": item, "category": "unknown"})
                elif isinstance(item, dict):
                    result.append(item)
            return result
    except json.JSONDecodeError:
        return []


def save_kb(kb: list[dict], phase: int = None):
    """
    持久化知识库到文件。
    如果指定 phase，保存到对应阶段的知识库文件。
    """
    if phase is not None:
        os.makedirs(config.KB_DIR, exist_ok=True)
        kb_file = os.path.join(config.KB_DIR, f"knowledge_base_phase_{phase}.json")
    else:
        kb_file = config.KB_FILE

    with open(kb_file, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)


def load_all_previous_kb(current_phase: int) -> list[dict]:
    """加载当前阶段之前所有阶段的知识库"""
    all_kb = []
    for p in range(1, current_phase):
        kb = load_kb(phase=p)
        for entry in kb:
            entry_with_phase = dict(entry)
            entry_with_phase["from_phase"] = p
            all_kb.append(entry_with_phase)
    return all_kb


def get_aggregated_kb(current_phase: int, current_kb: list[dict]) -> list[dict]:
    """
    获取汇总后的知识库（历史阶段 + 当前阶段）。
    用于给 LLM 提供完整上下文。
    """
    all_kb = load_all_previous_kb(current_phase)
    # 合并当前阶段知识（注意避免重复引用，虽然这里是新建列表）
    all_kb.extend(current_kb)
    return all_kb
