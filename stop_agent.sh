#!/bin/bash
# 停止智能体：先停 watchdog（防止重启），再 TERM 优雅退出，超时 KILL。

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR="$DIR/logs/system"
PID_FILE="$LOG_DIR/agent.pid"
WATCHDOG_PID_FILE="$LOG_DIR/watchdog.pid"

echo "[*] 正在停止 Agent..."

# 1. 先停 watchdog，防止它把 agent 拉起来
if [ -f "$WATCHDOG_PID_FILE" ]; then
    WPID=$(cat "$WATCHDOG_PID_FILE")
    if kill -0 "$WPID" 2>/dev/null; then
        echo "[*] 终止 watchdog: $WPID"
        kill "$WPID" 2>/dev/null
    fi
    rm -f "$WATCHDOG_PID_FILE"
fi

# 2. TERM agent → 优雅退出（保存 checkpoint）
PIDS=$(pgrep -f "$DIR/agent.py")
if [ -z "$PIDS" ]; then
    PIDS=$(ps aux | grep '[a]gent.py' | awk '{print $2}')
fi

if [ -z "$PIDS" ]; then
    echo "[*] 未找到运行中的 Agent 进程"
    rm -f "$PID_FILE"
    exit 0
fi

for PID in $PIDS; do
    echo "[*] 发送 SIGTERM: $PID（等待优雅退出，最多 30 秒）"
    kill -TERM "$PID" 2>/dev/null
done

for i in $(seq 1 30); do
    ALIVE=""
    for PID in $PIDS; do
        kill -0 "$PID" 2>/dev/null && ALIVE="$ALIVE $PID"
    done
    [ -z "$ALIVE" ] && break
    sleep 1
done

for PID in $PIDS; do
    if kill -0 "$PID" 2>/dev/null; then
        echo "[!] 进程 $PID 未在 30 秒内退出，强制 KILL"
        kill -9 "$PID" 2>/dev/null
    fi
done

rm -f "$PID_FILE"
echo "[*] Agent 已停止"
