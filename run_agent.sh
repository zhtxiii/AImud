#!/bin/bash
# 启动智能体（watchdog 监督模式）
#
# 用法：
#   AGENT_MODEL=1 ./run_agent.sh          # 后台 watchdog 模式（无人值守）
#   AGENT_MODEL=1 ./run_agent.sh --fg     # 前台直跑（调试用，无 watchdog）
#
# 环境变量：
#   AGENT_MODEL   1=DeepSeek 2=Polo（无人值守必须设置）
#   AGENT_MODE    grind(默认)/explore
#   PYTHON_BIN    指定 Python 解释器

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

LOG_DIR="logs/system"
LOG_FILE="$LOG_DIR/runtime.log"
WATCHDOG_LOG="$LOG_DIR/watchdog.log"
ALERT_FILE="$LOG_DIR/ALERT"
PID_FILE="$LOG_DIR/agent.pid"
WATCHDOG_PID_FILE="$LOG_DIR/watchdog.pid"

mkdir -p "$LOG_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[!] 未找到 Python 解释器: $PYTHON_BIN"
    exit 1
fi

# ---- 启动前自检 ----
if [ -z "$AGENT_MODEL" ]; then
    echo "[!] 未设置 AGENT_MODEL（1=DeepSeek 2=Polo）。无人值守运行必须设置，否则模型选择会阻塞。"
    exit 1
fi

if [ "${AGENT_MODE:-grind}" = "grind" ]; then
    for f in data/world_map.json data/npc_index.json; do
        if [ ! -f "$f" ]; then
            echo "[!] 缺少离线资产 $f，请先运行 tools/build_world.py / tools/build_npc_index.py"
            exit 1
        fi
    done
fi

if pgrep -f "$DIR/agent.py" >/dev/null 2>&1 || { [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }; then
    echo "[!] 已有 agent 在运行，请先 ./stop_agent.sh"
    exit 1
fi

rm -f "$ALERT_FILE"

# ---- 前台模式（调试） ----
if [ "$1" = "--fg" ]; then
    exec "$PYTHON_BIN" -u agent.py
fi

# ---- watchdog 模式 ----
watchdog() {
    local restarts=0
    local window_start=$(date +%s)

    while true; do
        # 日志按天分文件
        local day_log="$LOG_DIR/runtime-$(date +%Y%m%d).log"
        ln -sf "$(basename "$day_log")" "$LOG_FILE" 2>/dev/null

        echo "[$(date '+%F %T')] [watchdog] 启动 agent.py" >> "$WATCHDOG_LOG"
        "$PYTHON_BIN" -u agent.py >> "$day_log" 2>&1 &
        local pid=$!
        echo "$pid" > "$PID_FILE"
        wait "$pid"
        local code=$?
        rm -f "$PID_FILE"

        echo "[$(date '+%F %T')] [watchdog] agent.py 退出，code=$code" >> "$WATCHDOG_LOG"

        # 正常退出（0=完成/目标达成）不重启
        if [ "$code" -eq 0 ]; then
            echo "[$(date '+%F %T')] [watchdog] 正常退出，watchdog 结束" >> "$WATCHDOG_LOG"
            break
        fi

        # 重启限频：1 小时窗口内超过 6 次 → 停止并告警
        local now=$(date +%s)
        if [ $((now - window_start)) -gt 3600 ]; then
            window_start=$now
            restarts=0
        fi
        restarts=$((restarts + 1))
        if [ "$restarts" -gt 6 ]; then
            echo "[$(date '+%F %T')] [watchdog] ALERT: 1小时内重启超过6次，停止重启！" | tee -a "$WATCHDOG_LOG" > "$ALERT_FILE"
            break
        fi

        sleep 10
    done
    rm -f "$WATCHDOG_PID_FILE"
}

echo "[*] 以 watchdog 模式后台启动 Agent (AGENT_MODE=${AGENT_MODE:-grind}, AGENT_MODEL=$AGENT_MODEL)..."
export AGENT_MODEL AGENT_MODE
nohup bash -c "$(declare -f watchdog); LOG_DIR='$LOG_DIR' LOG_FILE='$LOG_FILE' WATCHDOG_LOG='$WATCHDOG_LOG' ALERT_FILE='$ALERT_FILE' PID_FILE='$PID_FILE' WATCHDOG_PID_FILE='$WATCHDOG_PID_FILE' PYTHON_BIN='$PYTHON_BIN'; cd '$DIR'; watchdog" >> "$WATCHDOG_LOG" 2>&1 &
WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > "$WATCHDOG_PID_FILE"

sleep 2
if ! kill -0 "$WATCHDOG_PID" 2>/dev/null; then
    echo "[!] watchdog 启动失败，请查看 $WATCHDOG_LOG"
    exit 1
fi

echo "[*] watchdog PID: $WATCHDOG_PID（agent PID 见 $PID_FILE）"
echo "[*] 运行日志: tail -f $DIR/$LOG_FILE"
echo "[*] 进度监控: ./status.sh"
