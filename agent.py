"""
MUD 自主智能体 - 主入口
基于 LangGraph 的 规划者驱动 + 阶段化架构。

grind 模式（默认）：里程碑驱动，目标 = 把角色练到 GOAL_EXP 实战经验。
explore 模式：原有开放式探索。
"""
import os
import signal
import subprocess
import sys
import time

import config
import persistence
import runtime_control
from config import Colors
from connection_manager import SocketClient
from llm_client import LLMClient, LLMFailure
from graph import build_graph
from nodes import log_colored, load_kb
from nodes.reflector import _load_experiences

try:
    from langgraph.errors import GraphRecursionError
except ImportError:  # 兼容旧版 langgraph
    class GraphRecursionError(Exception):
        pass


def _install_signal_handlers():
    def handler(signum, frame):
        sig_name = signal.Signals(signum).name
        print(f"\n{Colors.YELLOW}[系统] 收到 {sig_name}，准备优雅退出...{Colors.RESET}")
        runtime_control.request_stop()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _maybe_start_mud() -> bool:
    """AUTO_START_MUD 开启时尝试启动本地 MUD。返回是否执行了启动。"""
    if not config.AUTO_START_MUD:
        return False
    # 优先用带 libevent 兼容垫片的包装脚本
    wrapper = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "tools", "start_mud.sh")
    start_script = wrapper if os.path.exists(wrapper) else \
        os.path.join(config.MUD_PROJECT_DIR, "bin", "startmud")
    if not os.path.exists(start_script):
        log_colored("系统", f"未找到 MUD 启动脚本: {start_script}", Colors.RED)
        return False
    log_colored("系统", "尝试启动本地 MUD 服务器...", Colors.YELLOW)
    try:
        subprocess.run([start_script], cwd=os.path.dirname(start_script),
                       timeout=30, capture_output=True)
        time.sleep(8)  # 等待驱动加载 mudlib
        return True
    except Exception as e:
        log_colored("系统", f"启动 MUD 失败: {e}", Colors.RED)
        return False


def _build_initial_state(client, llm) -> dict:
    """构建初始状态；存在 checkpoint 时恢复任务进度。"""
    state = {
        "client": client,
        "llm": llm,
        "server_output": "",
        "server_output_clean": "",
        "history": [],
        "knowledge_base": load_kb(phase=1),
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
        # grind 模式字段
        "char_status": {},
        "milestone": {},
        "exp_history": [],
        "credentials": persistence.load_credentials() or {},
        "escalation": {},
        "exit_reason": "none",
        "counters": {"deaths": 0, "quests_done": 0, "quests_skipped": 0,
                     "reconnects": 0, "llm_failures": 0},
    }

    # 加载经验与技能
    exp_data = _load_experiences()
    state["experiences"] = exp_data.get("experiences", [])
    state["skills"] = exp_data.get("skills", [])
    log_colored("系统", f"已加载 {len(state['experiences'])} 条经验和 {len(state['skills'])} 个技能", Colors.WHITE)

    # grind 模式下环境已知，跳过环境识别
    if config.AGENT_MODE == "grind":
        state["environment_type"] = "text_mud"

    checkpoint = persistence.load_checkpoint()
    if checkpoint:
        restorable = {k: v for k, v in checkpoint.items() if k in state or k in (
            "phase", "phase_name", "tasks", "current_task", "completed_phases",
            "char_status", "milestone", "exp_history", "counters", "environment_type",
            "task_attempts",
        )}
        state.update(restorable)
        # 控制流字段不从 checkpoint 恢复
        state["exit_reason"] = "none"
        state["task_completed"] = False
        state["task_stuck"] = False
        # 升级计数器每次进程重启清零（fatal 的意义就是换个进程重试）
        cnt = state.get("counters") or {}
        for k in list(cnt.keys()):
            if k.startswith("esc_") or k == "repair_failures":
                cnt[k] = 0
        # 重置停滞计时：补一个"当前时刻同经验"续点，避免宕机时长被算成停滞→fatal 循环
        hist = state.get("exp_history") or []
        if hist:
            hist.append([time.time(), hist[-1][1]])
        ms = state.get("milestone", {}).get("id", "?")
        exp = state.get("char_status", {}).get("exp", "?")
        log_colored("系统", f"已从 checkpoint 恢复进度：里程碑 {ms}，经验 {exp}", Colors.GREEN)

    return state


def _reset_transient_fields(state: dict, client, llm):
    """每次（重）连接前重置连接与控制流相关字段。"""
    state["client"] = client
    state["llm"] = llm
    state["server_output"] = ""
    state["server_output_clean"] = ""
    state["should_reconnect"] = False
    state["should_stop"] = False
    state["should_exit"] = False
    state["exit_reason"] = "none"
    state["action_type"] = "wait"
    state["payload"] = ""
    state["expected_result"] = ""
    state["last_client_payload"] = ""
    state["kb_update_future"] = None
    # logged_in 是连接级状态：每次重连必须重新走登录例程
    if isinstance(state.get("char_status"), dict):
        state["char_status"]["logged_in"] = False


def main():
    log_colored("系统", f"正在启动自主智能体（{config.AGENT_MODE} 模式），目标：{config.TARGET_IP}:{config.TARGET_PORT}", Colors.WHITE)
    if config.AGENT_MODE == "grind":
        log_colored("系统", f"任务目标：实战经验 ≥ {config.GOAL_EXP}", Colors.WHITE)

    _install_signal_handlers()

    provider_config = config.select_model()
    llm = LLMClient(provider_config=provider_config)
    log_colored("系统", f"LLM 客户端已初始化: {provider_config['name']}", Colors.WHITE)
    client = SocketClient()

    os.makedirs(config.KB_DIR, exist_ok=True)
    for subdir in ["system", "planner", "knowledge", "tasks", "reflector"]:
        os.makedirs(os.path.join(config.LOG_DIR, subdir), exist_ok=True)
    os.makedirs(config.REFLECTIONS_DIR, exist_ok=True)

    compiled_graph = build_graph()
    log_colored("系统", "LangGraph 状态图已编译", Colors.WHITE)

    current_state = _build_initial_state(client, llm)

    connect_failures = 0
    mud_start_attempted = False
    backoff = 5

    while not runtime_control.stop_requested():
        try:
            if not client.connect():
                connect_failures += 1
                if connect_failures >= 3 and not mud_start_attempted:
                    mud_start_attempted = _maybe_start_mud()
                    if mud_start_attempted:
                        continue
                log_colored("系统", f"连接失败（{connect_failures} 次），{backoff} 秒后重试...", Colors.RED)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue

            connect_failures = 0
            mud_start_attempted = False
            backoff = 5

            _reset_transient_fields(current_state, client, llm)

            # 从磁盘重新加载当前阶段知识库（防止断连丢失）
            current_phase = current_state.get("phase", 1)
            current_state["knowledge_base"] = load_kb(phase=current_phase)

            log_colored("系统", f"当前进度：阶段 {current_state.get('phase')} - {current_state.get('phase_name')}", Colors.WHITE)

            final_state = compiled_graph.invoke(
                current_state, config={"recursion_limit": 1000}
            )
            current_state = dict(final_state)
            persistence.save_checkpoint(current_state)

            exit_reason = current_state.get("exit_reason", "none")
            if current_state.get("should_stop") or exit_reason == "stop" or runtime_control.stop_requested():
                log_colored("系统", "智能体停止。", Colors.WHITE)
                break

            if exit_reason == "goal_reached":
                log_colored("系统", f"🎉 目标达成！实战经验 ≥ {config.GOAL_EXP}", Colors.GREEN)
                break

            if exit_reason == "fatal":
                log_colored("系统", "致命状态，退出交由 watchdog 重启。", Colors.RED)
                sys.exit(3)

            if current_state.get("should_exit"):
                env_type = current_state.get("environment_type", "unknown")
                log_colored("系统", f"检测到非文本交互环境 ({env_type})，智能体退出。", Colors.YELLOW)
                break

            # 否则是 should_reconnect / reconnect
            counters = current_state.setdefault("counters", {})
            counters["reconnects"] = counters.get("reconnects", 0) + 1
            log_colored("系统", "连接断开，5秒后重连...", Colors.YELLOW)
            time.sleep(5)

        except GraphRecursionError:
            log_colored("系统", "LangGraph 递归上限触发，保存进度后按重连处理。", Colors.RED)
            persistence.save_checkpoint(current_state)
            time.sleep(3)
        except KeyboardInterrupt:
            print(f"\n{Colors.WHITE}[!] 用户中断。{Colors.RESET}")
            break
        except LLMFailure as e:
            counters = current_state.setdefault("counters", {})
            counters["llm_failures"] = counters.get("llm_failures", 0) + 1
            log_colored("系统", f"LLM 持续失败：{e}。保存进度，60 秒后重试...", Colors.RED)
            persistence.save_checkpoint(current_state)
            time.sleep(60)
        except Exception as e:
            log_colored("系统", f"未捕获异常：{type(e).__name__}: {e}。保存进度，5秒后重启...", Colors.RED)
            persistence.save_checkpoint(current_state)
            time.sleep(5)
        finally:
            client.disconnect()

    persistence.save_checkpoint(current_state)
    log_colored("系统", "进度已保存，进程退出。", Colors.WHITE)
    # 退出码语义：0=目标达成/不可恢复的正常结束（watchdog 不再重启）
    #            2=信号停止/其他（配合 stop_agent.sh：先杀 watchdog 再 TERM 则不会被拉起）
    if current_state.get("exit_reason") == "goal_reached":
        sys.exit(0)
    if current_state.get("should_exit"):
        sys.exit(0)
    sys.exit(2)


if __name__ == "__main__":
    main()
