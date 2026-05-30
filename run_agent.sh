#!/bin/bash

# 定义日志文件
LOG_FILE="logs/system/runtime.log"

# 获取脚本所在目录
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 切换到脚本目录
cd "$DIR"

# 确保日志目录存在
mkdir -p logs/system

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[!] 未找到 Python 解释器: $PYTHON_BIN"
    echo "[!] 请安装 python3，或通过 PYTHON_BIN=/path/to/python 指定解释器"
    exit 1
fi

echo "[*] 正在启动 Agent (LangGraph)..."

# 后台运行 agent.py
# -u: 禁用 python 缓冲
# 2>&1: 合并 stderr 到 stdout
nohup "$PYTHON_BIN" -u agent.py >> "$LOG_FILE" 2>&1 &

# 获取 PID
PID=$!

sleep 1
if ! kill -0 "$PID" 2>/dev/null; then
    echo "[!] Agent 启动后立即退出，请查看日志：$DIR/$LOG_FILE"
    tail -n 40 "$LOG_FILE" 2>/dev/null || true
    exit 1
fi

echo "[*] Agent 已在后台启动，PID: $PID"
echo "[*] 运行日志将写入: $DIR/$LOG_FILE"
echo "[*] 交互日志将写入: $DIR/logs/system/interaction.log"
echo "[*] 使用 'tail -f $LOG_FILE' 查看运行状态"
