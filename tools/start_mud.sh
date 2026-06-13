#!/bin/bash
# 启动本地 MUD（带 libevent 兼容垫片 + 单实例守卫）。
# 预编译 driver 链接 libevent-2.0.so.5，系统只有 2.1 → 用户目录符号链接兼容。
MUD_DIR="${MUD_PROJECT_DIR:-$HOME/project}"
COMPAT="$HOME/.local/lib/mudcompat"

# 单实例守卫：双驱动会共写 swap 文件导致运行时腐化（eval_cost 爆炸/拒绝连接）
EXISTING=$(pgrep -x driver)
if [ -n "$EXISTING" ]; then
    echo "[start_mud] 发现已有 driver 进程 ($EXISTING)，先停止..."
    pgrep -x driver | xargs -r kill
    sleep 2
    pgrep -x driver | xargs -r kill -9 2>/dev/null
    sleep 1
fi
rm -f "$MUD_DIR"/mudlib/adm/tmp/._swapfile.* "$MUD_DIR"/bin/mudos.pid

if [ ! -e "$COMPAT/libevent-2.0.so.5" ]; then
    mkdir -p "$COMPAT"
    ln -sf /usr/lib/x86_64-linux-gnu/libevent-2.1.so.7 "$COMPAT/libevent-2.0.so.5"
fi

export LD_LIBRARY_PATH="$COMPAT${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$MUD_DIR/bin" && ./startmud
