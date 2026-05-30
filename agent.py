"""
MUD 自主智能体 - 主入口
基于 LangGraph 的 规划者驱动 + 阶段化架构。
"""
import os
import sys
import time

import config
from config import Colors
from connection_manager import SocketClient
from llm_client import LLMClient
from graph import build_graph
from nodes import log_colored, load_kb
from nodes.reflector import _load_experiences


def main():
    log_colored("系统", f"正在启动自主智能体，目标：{config.TARGET_IP}:{config.TARGET_PORT}", Colors.WHITE)

    # 初始化组件
    llm = LLMClient()
    client = SocketClient()

    # 确保知识库目录存在
    os.makedirs(config.KB_DIR, exist_ok=True)

    # 确保日志目录结构存在
    log_subdirs = ["system", "planner", "knowledge", "tasks", "reflector"]
    for subdir in log_subdirs:
        os.makedirs(os.path.join(config.LOG_DIR, subdir), exist_ok=True)
        
    # 确保反思存储目录存在
    os.makedirs(config.REFLECTIONS_DIR, exist_ok=True)

    # 编译 LangGraph 图
    compiled_graph = build_graph()
    log_colored("系统", "LangGraph 状态图已编译", Colors.WHITE)

    # 构建初始状态（只在首次启动时使用，重连时会保留进度）
    # 加载现有经验和技能
    exp_data = _load_experiences()
    initial_exp = exp_data.get("experiences", [])
    initial_skills = exp_data.get("skills", [])
    log_colored("系统", f"已加载 {len(initial_exp)} 条经验和 {len(initial_skills)} 个技能", Colors.WHITE)

    current_state = {
        "client": client,
        "llm": llm,
        "server_output": "",
        "server_output_clean": "",
        "history": [],
        "knowledge_base": load_kb(phase=1),  # 加载阶段1知识库
        "phase": 1,
        "phase_name": "环境识别",
        "tasks": [],
        "current_task": {},
        "completed_phases": [],
        "environment_type": "unknown",
        "analysis": "",
        "action_type": "wait",
        "payload": "",
        "expected_result": "",
        "last_client_payload": "",
        "should_reconnect": False,
        "should_stop": False,
        "should_exit": False,
        "task_completed": False,
        "kb_consolidation_counter": 0,
    }

    while True:  # 外层重连循环
        try:
            # 尝试连接
            if not client.connect():
                print(f"{Colors.RED}[系统] 5秒后重试...{Colors.RESET}")
                time.sleep(5)
                continue

            # 重连时：重置连接/控制流字段，保留任务进度
            current_state["client"] = client
            current_state["llm"] = llm
            current_state["server_output"] = ""
            current_state["server_output_clean"] = ""
            current_state["should_reconnect"] = False
            current_state["should_stop"] = False
            current_state["should_exit"] = False
            current_state["action_type"] = "wait"
            current_state["payload"] = ""
            current_state["expected_result"] = ""
            current_state["last_client_payload"] = ""
            current_state["kb_update_future"] = None

            # 从磁盘重新加载当前阶段知识库（防止断连丢失）
            current_phase = current_state.get("phase", 1)
            current_state["knowledge_base"] = load_kb(phase=current_phase)

            log_colored("系统", f"当前进度：阶段 {current_state['phase']} - {current_state['phase_name']}", Colors.WHITE)

            # 运行 LangGraph 图
            log_colored("系统", "开始规划者驱动循环...", Colors.WHITE)
            final_state = compiled_graph.invoke(current_state)

            # 图退出后，保留 final_state 作为下次重连的基础
            current_state = dict(final_state)

            # 图退出 → 检查原因
            if final_state.get("should_stop", False):
                log_colored("系统", "智能体主动停止。", Colors.WHITE)
                break

            if final_state.get("should_exit", False):
                env_type = final_state.get("environment_type", "unknown")
                log_colored("系统", f"检测到非文本交互环境 ({env_type})，智能体退出。", Colors.YELLOW)
                break

            # 否则是 should_reconnect，进行重连
            log_colored("系统", "连接断开，5秒后重连...", Colors.YELLOW)
            time.sleep(5)

        except KeyboardInterrupt:
            print(f"\n{Colors.WHITE}[!] 用户中断。{Colors.RESET}")
            break
        except Exception as e:
            print(f"{Colors.RED}[!] 发生未捕获异常：{e}。5秒后重启...{Colors.RESET}")
            time.sleep(5)
        finally:
            client.disconnect()


if __name__ == "__main__":
    main()
