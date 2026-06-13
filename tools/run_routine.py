#!/usr/bin/env python3
"""
单例程测试工具：绕开 LangGraph 直接连 MUD 跑指定例程（也是速率标定工具）。

用法：
  AGENT_MODEL=1 python3 tools/run_routine.py --routine login
  AGENT_MODEL=1 python3 tools/run_routine.py --routine spar --minutes 30
  AGENT_MODEL=1 python3 tools/run_routine.py --routine navigate --dest god2
  python3 tools/run_routine.py --routine spar --no-llm   # 不初始化 LLM（例程不依赖时）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import persistence
from connection_manager import SocketClient
from mud.routines import REGISTRY
from mud.routines.base import RoutineContext


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routine", required=True, choices=sorted(REGISTRY.keys()))
    ap.add_argument("--minutes", type=float, default=None, help="budget_min 参数")
    ap.add_argument("--target-exp", type=int, default=None)
    ap.add_argument("--dest", default=None, help="navigate 目的地（锚点名或路径）")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    params = {}
    if args.minutes is not None:
        params["budget_min"] = args.minutes
    if args.target_exp is not None:
        params["target_exp"] = args.target_exp
    if args.dest:
        params["dest"] = args.dest

    llm = None
    if not args.no_llm:
        from llm_client import LLMClient
        llm = LLMClient(provider_config=config.select_model())

    client = SocketClient()
    if not client.connect():
        print("无法连接 MUD，请先启动（~/project/bin/startmud）")
        sys.exit(1)

    state = {
        "client": client,
        "llm": llm,
        "char_status": {},
        "credentials": persistence.load_credentials() or {},
        "counters": {},
        "exp_history": [],
        "milestone": {"id": f"TEST-{args.routine}"},
    }
    ckpt = persistence.load_checkpoint()
    if ckpt:
        for k in ("char_status", "counters", "exp_history"):
            if ckpt.get(k):
                state[k] = ckpt[k]
        # 重置停滞计时（与 agent.py 一致）：避免旧时间戳触发例程内的停滞判定
        if state["exp_history"]:
            state["exp_history"].append([time.time(), state["exp_history"][-1][1]])
        print(f"[test] 已载入 checkpoint（exp={state['char_status'].get('exp')}）")

    # 非 login 例程需要先登录
    if args.routine != "login":
        print("[test] 先执行登录例程...")
        ctx0 = RoutineContext(state)
        r0 = REGISTRY["login"]().run(ctx0, {})
        print(f"[test] login → {r0.outcome}: {r0.detail}")
        if r0.outcome != "completed":
            sys.exit(2)

    ctx = RoutineContext(state)
    start_exp = state["char_status"].get("exp", 0)
    t0 = time.time()
    result = REGISTRY[args.routine]().run(ctx, params)
    dt = time.time() - t0

    end_exp = state["char_status"].get("exp", start_exp)
    print("\n" + "=" * 60)
    print(f"例程: {args.routine} | 结果: {result.outcome}")
    print(f"详情: {result.detail}")
    print(f"耗时: {dt / 60:.1f} 分钟 | 经验: {start_exp} → {end_exp} "
          f"(Δ{end_exp - start_exp}, ≈{(end_exp - start_exp) / max(dt, 1) * 3600:.0f}/h)")
    if result.state_updates.get("escalation"):
        print(f"升级上下文: {json.dumps(result.state_updates['escalation'], ensure_ascii=False)[:400]}")
    persistence.save_checkpoint(state)
    client.disconnect()


if __name__ == "__main__":
    main()
