"""
观察节点模块
从 Socket 接收服务器输出并进行清洗。
"""
import re

from state import AgentState
from nodes.helpers import log_colored


def _filter_compiler_warnings(text: str) -> str:
    """过滤 MUD 编译警告块，保留真实游戏输出。"""
    lines = text.splitlines()
    kept = []
    skipping = False

    for line in lines:
        if "编译时段错误" in line and "Warning:" in line:
            skipping = True
            continue
        if skipping:
            if "^" in line:
                skipping = False
            continue
        kept.append(line)

    return "\n".join(kept)


def observe(state: AgentState) -> dict:
    """
    观察节点：从 Socket 接收服务器输出。

    如果连接断开（receive 返回 None），设置 should_reconnect=True。
    """
    client = state["client"]
    history = list(state.get("history", []))
    last_client_payload = state.get("last_client_payload", "")

    server_output = client.receive()

    if server_output is None:
        return {
            "server_output": "",
            "server_output_clean": "",
            "should_reconnect": True,
        }

    server_output_clean = client.clean_ansi(server_output)
    # 过滤编译器警告
    server_output_clean = _filter_compiler_warnings(server_output_clean)

    # 过滤 Telnet 协商乱码
    server_output_clean = re.sub(r'(?m)^.*VF\*Z.*$', '', server_output_clean)

    server_output_clean = server_output_clean.strip()

    if server_output_clean:
        log_colored("服务器", server_output_clean)

    if server_output_clean:
        if last_client_payload:
            history.append(f"Response to {last_client_payload}: {server_output_clean[:500]}")
            last_client_payload = ""
        else:
            history.append(f"Server: {server_output_clean[:500]}")

    return {
        "server_output": server_output,
        "server_output_clean": server_output_clean,
        "history": history,
        "last_client_payload": last_client_payload,
        "should_reconnect": False,
    }
