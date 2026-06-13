#!/bin/bash
# 长跑健康检查：给会话级监控/cron 调用。
# 输出一行状态摘要；检测到异常时输出 ALERT 行。
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/.."
cd "$DIR"

CSV="logs/system/progress.csv"
NOW=$(date +%s)

# agent 进程（优先 pid 文件）
AGENT_PID=$(cat logs/system/agent.pid 2>/dev/null)
if [ -n "$AGENT_PID" ] && ! kill -0 "$AGENT_PID" 2>/dev/null; then
    AGENT_PID=""
fi
[ -z "$AGENT_PID" ] && AGENT_PID=$(pgrep -f "python3 -u agent[.]py" | head -1)
WD_PID=$(cat logs/system/watchdog.pid 2>/dev/null)

# 存档经验（外部真值）
CHAR_ID=$(python3 -c "import json;print(json.load(open('data/credentials.json'))['id'])" 2>/dev/null)
FILE_EXP=""
if [ -n "$CHAR_ID" ]; then
    SAVE="$HOME/project/mudlib/data/user/${CHAR_ID:0:1}/$CHAR_ID.o"
    [ -f "$SAVE" ] && FILE_EXP=$(grep -o '"combat_exp":[0-9]*' "$SAVE" | grep -o '[0-9]*$')
fi

# progress.csv 最新行
LAST=""
RATE=""
AGE=""
if [ -f "$CSV" ]; then
    LAST=$(tail -1 "$CSV")
    TS=$(echo "$LAST" | cut -d, -f1)
    EXP=$(echo "$LAST" | cut -d, -f2)
    RATE=$(echo "$LAST" | cut -d, -f3)
    [ -n "$TS" ] && [[ "$TS" =~ ^[0-9]+$ ]] && AGE=$(( NOW - TS ))
fi

echo "STATUS agent=${AGENT_PID:-DOWN} watchdog=${WD_PID:-无} 存档exp=${FILE_EXP:-?} csv_exp=${EXP:-?} rate=${RATE:-?}/h csv_age=${AGE:-?}s"

# 告警判定
[ -f logs/system/ALERT ] && echo "ALERT watchdog停止重启: $(cat logs/system/ALERT)"
if [ -z "$AGENT_PID" ] && [ -n "$WD_PID" ] && ! kill -0 "$WD_PID" 2>/dev/null; then
    echo "ALERT agent 和 watchdog 都未运行"
fi
if [ -n "$AGE" ] && [ "$AGE" -gt 2700 ]; then
    echo "ALERT progress.csv 已 $((AGE/60)) 分钟未更新（可能卡死）"
fi
if ! pgrep -x driver >/dev/null; then
    echo "ALERT MUD driver 未运行"
fi
DEATHS=$(wc -l < logs/system/deaths.log 2>/dev/null || echo 0)
echo "DEATHS=$DEATHS"
