"""
行动节点模块
发送 Payload 到服务器，更新交互历史。
"""
import time

from config import Colors
from state import AgentState
from nodes.helpers import log_colored


def act(state: AgentState) -> dict:
    """
    行动节点：发送 Payload 到服务器，更新交互历史。
    """
    client = state["client"]
    action_type = state.get("action_type", "send")
    payload = state.get("payload", "")
    history = list(state.get("history", []))  # 拷贝
    expected_result = state.get("expected_result", "")

    if action_type == "enter":
        log_colored("客户端", "发送：<ENTER>", Colors.GREEN)
        if client.send(""):
            history.append(f"Action: ENTER | Expected: {expected_result[:100]}")
            last_payload = "<ENTER>"
        else:
            return {
                "history": history,
                "should_reconnect": True,
            }
    elif action_type == "send" and payload:
        log_colored("客户端", f"发送：{payload}", Colors.GREEN)
        if client.send(payload):
            history.append(f"Action: SEND {payload} | Expected: {expected_result[:100]}")
            last_payload = payload
        else:
            # 发送失败 → 触发重连
            return {
                "history": history,
                "should_reconnect": True,
            }
    else:
        log_colored("分析", "决定等待，不发送任何内容。", Colors.CYAN)
        last_payload = ""

    # 节奏控制
    time.sleep(1)

    return {
        "history": history,
        "last_client_payload": last_payload,
        "should_reconnect": False,
    }
