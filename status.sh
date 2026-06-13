#!/bin/bash
# 进度监控：速率/ETA + 存档外部验证 + 告警状态
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

GOAL=${GOAL_EXP:-100000}
MUD_DIR="${MUD_PROJECT_DIR:-$HOME/project}"
CSV="logs/system/progress.csv"
CRED="data/credentials.json"

echo "==================== 10万经验进度 ===================="

# 角色与存档验证
if [ -f "$CRED" ]; then
    CHAR_ID=$(python3 -c "import json;print(json.load(open('$CRED'))['id'])" 2>/dev/null)
    if [ -n "$CHAR_ID" ]; then
        SAVE="$MUD_DIR/mudlib/data/user/${CHAR_ID:0:1}/$CHAR_ID.o"
        if [ -f "$SAVE" ]; then
            FILE_EXP=$(grep -o '"combat_exp":[0-9]*' "$SAVE" | grep -o '[0-9]*')
            AGE=$(( $(date +%s) - $(stat -c %Y "$SAVE") ))
            echo "角色: $CHAR_ID | 存档经验: ${FILE_EXP:-0} / $GOAL | 存档更新于 ${AGE}s 前"
        else
            echo "角色: $CHAR_ID | 尚无存档文件"
        fi
    fi
else
    echo "尚未生成角色凭据"
fi

# 进度曲线与速率
if [ -f "$CSV" ]; then
    python3 - "$CSV" "$GOAL" <<'EOF'
import csv, sys, time
rows = list(csv.DictReader(open(sys.argv[1])))
goal = int(sys.argv[2])
if rows:
    last = rows[-1]
    now = time.time()
    exp = int(last["exp"]); ts = int(last["ts"])
    # 1小时窗口速率
    win = [r for r in rows if now - int(r["ts"]) <= 3600]
    rate = 0.0
    if len(win) >= 2:
        dt = int(win[-1]["ts"]) - int(win[0]["ts"])
        if dt > 0:
            rate = (int(win[-1]["exp"]) - int(win[0]["exp"])) * 3600 / dt
    eta = (goal - exp) / rate if rate > 0 else float("inf")
    print(f"最新记录: exp={exp} 里程碑={last['milestone']} ({int(now-ts)}s 前)")
    print(f"1h 速率: {rate:.0f} exp/h | ETA: {'%.1f 小时' % eta if eta != float('inf') else 'N/A'}")
    print(f"任务: 完成 {last['quests_done']} / 跳过 {last['quests_skipped']} | 死亡: {last['deaths']}")
else:
    print("progress.csv 为空")
EOF
else
    echo "progress.csv 尚未生成"
fi

# 进程与告警
AGENT_PID=$(pgrep -f "$DIR/agent.py" | head -1)
echo "Agent 进程: ${AGENT_PID:-未运行} | watchdog: $( [ -f logs/system/watchdog.pid ] && cat logs/system/watchdog.pid || echo 无 )"
[ -f logs/system/ALERT ] && echo "⚠️ ALERT: $(cat logs/system/ALERT)"
[ -f logs/system/deaths.log ] && echo "死亡记录: $(wc -l < logs/system/deaths.log) 条（tail -3）" && tail -3 logs/system/deaths.log
RESTARTS=$(grep -c "启动 agent.py" logs/system/watchdog.log 2>/dev/null || echo 0)
echo "watchdog 累计启动次数: $RESTARTS"
echo "======================================================="
