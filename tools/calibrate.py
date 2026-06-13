#!/usr/bin/env python3
"""
切磋速率标定工具：对若干候选目标各试打 N 分钟，输出实测 exp/h 排名。

机制要点（combatd.c 核验）：
- 攻击命中 + 我方ap<对方dp → +1 exp +1 pot（77%）；并免费成长攻击技能
- 我方闪避 + 我方dp<对方ap → +1 exp（61%）
- 被重击 random(max_kee+kee)<damage → +1 exp
- 切磋在任一方造成有效伤害时立刻"承让"散场 → 高频重新接战
理想目标 = dp 高于我方 ap（攻击通道开）且其 ap 低（打不疼我，无恢复停机）。

用法：python3 tools/calibrate.py --minutes 8   # 每个候选打 8 分钟
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
from mud import profile
from mud.profile import cmd
from mud.routines import REGISTRY
from mud.routines.base import RoutineContext
from mud.routines.navigate import goto, ARRIVED
from mud.world import get_world


def pick_candidates(ctx, my_ap: int, limit: int = 4) -> list[dict]:
    """从 npc_index 选标定候选：dp>my_ap、徒手、非野兽/aggressive、可达、tank 优先。"""
    world = ctx.world
    here = ctx.char.get("location_node") or "d/snow/inn"
    cands = []
    for cn, entries in world.npc_index.items():
        for e in entries:
            if e.get("armed") or e.get("beast") or e.get("aggressive"):
                continue
            rooms = [r["room"] for r in e.get("rooms", []) if r["room"] in world.nodes]
            if not rooms:
                continue
            dp, ap = e.get("dp_est", 0), e.get("ap_est", 0)
            if dp <= my_ap:          # 攻击通道关闭
                continue
            dist = world.steps_between(here, rooms[0])
            if dist < 0 or dist > 25:
                continue
            # tank 评分：dp 适中（1.2~30×my_ap）、对方 ap 越低越好、距离近
            band = 1.0 if dp <= max(300, my_ap * 30) else 0.2
            score = band * (1000.0 / (ap + 50)) * (10.0 / (dist + 10))
            cands.append({"cn": cn, "id": e["ids"][0] if e["ids"] else "",
                          "room": rooms[0], "dp": dp, "ap": ap,
                          "dist": dist, "score": score})
    cands.sort(key=lambda c: -c["score"])
    seen, out = set(), []
    for c in cands:
        if c["cn"] in seen or not c["id"]:
            continue
        seen.add(c["cn"])
        out.append(c)
        if len(out) >= limit:
            break
    return out


def spar_target(ctx, cand: dict, minutes: float) -> dict:
    """对单个目标持续 fight-重接 N 分钟，统计经验增长。"""
    if goto(ctx, cand["room"]) != ARRIVED:
        return {**cand, "exp_gain": 0, "note": "不可达"}
    sc = ctx.refresh_score() or {}
    start_exp = sc.get("exp", 0)
    deadline = time.time() + minutes * 60
    engages = refuses = 0
    note = ""
    while time.time() < deadline:
        text, events = ctx.io.request_events(cmd.fight(cand["id"]), quiet=0.3, deadline=3.0)
        if profile.has_event(events, "SELF_DEATH"):
            note = "死亡!"
            break
        if profile.has_event(events, "NO_FIGHT_ROOM"):
            note = "禁止战斗"
            break
        if profile.has_event(events, "FIGHT_REFUSED"):
            refuses += 1
            if refuses > 20:
                note = "持续拒战"
                break
            time.sleep(2)
            continue
        if profile.has_event(events, "NO_SUCH_TARGET"):
            ctx.refresh_room()
            rv = ctx.char.get("room_view") or {}
            if not any(o.get("id") == cand["id"] for o in rv.get("objects", [])):
                note = "目标不在"
                break
            continue
        engages += 1
        # 等本回合结束（承让/没动静）
        end_wait = time.time() + 6
        while time.time() < end_wait:
            t2 = ctx.io.drain(quiet=0.3, deadline=2.0)
            ev2 = profile.detect_events(t2)
            if profile.has_event(ev2, "SPAR_END", "FIGHT_REFUSED", "OPPONENT_DOWN"):
                break
        # 气检查
        hp = ctx.refresh_hp() or {}
        if hp.get("kee_pct", 100) < 50:
            t_rec = time.time()
            while hp.get("kee_pct", 100) < 85 and time.time() - t_rec < 240:
                time.sleep(8)
                hp = ctx.refresh_hp() or {}
    sc = ctx.refresh_score() or {}
    gain = sc.get("exp", start_exp) - start_exp
    return {**cand, "exp_gain": gain, "engages": engages,
            "rate_h": gain / minutes * 60 if minutes else 0, "note": note}


def main():
    ap_parser = argparse.ArgumentParser()
    ap_parser.add_argument("--minutes", type=float, default=8)
    ap_parser.add_argument("--targets", type=int, default=4)
    args = ap_parser.parse_args()

    client = SocketClient()
    if not client.connect():
        print("无法连接 MUD")
        sys.exit(1)
    state = {
        "client": client, "llm": None,
        "char_status": {}, "credentials": persistence.load_credentials() or {},
        "counters": {}, "exp_history": [], "milestone": {"id": "CALIB"},
    }
    ctx0 = RoutineContext(state)
    r = REGISTRY["login"]().run(ctx0, {})
    print(f"[calib] login → {r.outcome}")
    if r.outcome != "completed":
        sys.exit(2)

    ctx = RoutineContext(state)
    sc = ctx.refresh_score() or {}
    my_ap = sc.get("ap", max(1, sc.get("exp", 0) // 2))
    print(f"[calib] 我方 exp={sc.get('exp')} ap≈{my_ap} dp≈{sc.get('dp')}")

    cands = pick_candidates(ctx, my_ap, args.targets)
    print(f"[calib] 候选 {len(cands)} 个：")
    for c in cands:
        print(f"  {c['cn']}({c['id']}) dp={c['dp']} ap={c['ap']} 距离={c['dist']} @ {c['room']}")

    results = []
    for c in cands:
        print(f"\n[calib] ===== 测试 {c['cn']} {args.minutes} 分钟 =====")
        res = spar_target(ctx, c, args.minutes)
        print(f"[calib] {c['cn']}: +{res['exp_gain']} exp, {res.get('engages', 0)} 次接战, "
              f"≈{res.get('rate_h', 0):.0f}/h {res.get('note', '')}")
        results.append(res)

    print("\n========== 标定结果（按速率降序） ==========")
    for res in sorted(results, key=lambda x: -x.get("rate_h", 0)):
        print(f"  {res['rate_h']:>7.0f}/h  {res['cn']}({res['id']}) dp={res['dp']} ap={res['ap']} {res.get('note', '')}")
    persistence.save_checkpoint(state)
    client.disconnect()


if __name__ == "__main__":
    main()
