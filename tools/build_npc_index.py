#!/usr/bin/env python3
"""
离线生成 NPC 索引与派生配置：
- data/npc_index.json    全服 NPC：中文名→[{file,ids,rooms,exp_min,exp_max,skills,dp_est,ap_est,armed,beast,aggressive}]
- data/spar_ladder.json  切磋阶梯：徒手、非野兽、非主动攻击、可定位，按 dp_est 升序
- data/quest_whitelist.json  任务白名单：qlist 目标 ∩ 可定位

只读 mudlib。运行：python3 tools/build_npc_index.py（需先有 world_map.json）
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

MUDLIB = os.path.join(config.MUD_PROJECT_DIR, "mudlib")

_RE_INHERIT_NPC = re.compile(r'inherit\s+NPC\s*;')
_RE_SET_NAME = re.compile(r'set_name\(\s*"([^"]+)"\s*,\s*\(\{\s*([^}]*?)\s*\}\)')
_RE_IDS = re.compile(r'"([^"]+)"')
_RE_EXP = re.compile(r'set\("combat_exp"\s*,\s*([0-9+\-*/ ()random]+)\)')
_RE_SKILL = re.compile(r'set_skill\("([a-z\-]+)"\s*,\s*(\d+)\s*\)')
_RE_RACE_BEAST = re.compile(r'set\("race"\s*,\s*"野兽"')
_RE_ATTITUDE = re.compile(r'set\("attitude"\s*,\s*"([a-z]+)"')
_RE_WIELD = re.compile(r'->wield\(\)|command\("wield ')
_RE_APPLY = re.compile(r'set_temp\("apply/(dodge|attack|defense|damage)"\s*,\s*\(?(\d+)')
_RE_NUM = re.compile(r'(\d+)')
_RE_RANDOM = re.compile(r'random\(\s*(\d+)\s*\)')

_ATTACK_SKILLS = {"unarmed", "sword", "blade", "stick", "spear", "whip",
                  "club", "dagger", "axe", "hammer", "throwing", "cuff", "strike", "finger"}


def parse_exp_expr(expr: str) -> tuple[int, int]:
    """'600+random(400)' → (600, 999)；'100' → (100, 100)。"""
    expr = expr.strip()
    rand = 0
    m = _RE_RANDOM.search(expr)
    if m:
        rand = int(m.group(1))
        expr = _RE_RANDOM.sub("0", expr)
    nums = [int(n) for n in _RE_NUM.findall(expr)]
    base = nums[0] if nums else 0
    return base, base + max(0, rand - 1)


def power_est(skill_level: int, exp: int) -> int:
    """战力估算（combatd skill_power）：无技能时 = exp/2。"""
    if skill_level <= 0:
        return max(1, exp // 2)
    return skill_level ** 3 // 3 + exp


def scan_npcs() -> dict:
    """返回 rel_path → npc 信息。"""
    npcs = {}
    for base in (os.path.join(MUDLIB, "d"), os.path.join(MUDLIB, "u"),
                 os.path.join(MUDLIB, "daemon"), os.path.join(MUDLIB, "obj")):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fname in files:
                if not fname.endswith(".c"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except OSError:
                    continue
                if not _RE_INHERIT_NPC.search(content):
                    continue
                m_name = _RE_SET_NAME.search(content)
                if not m_name:
                    continue
                cn = m_name.group(1)
                ids = _RE_IDS.findall(m_name.group(2))
                m_exp = _RE_EXP.search(content)
                exp_min, exp_max = parse_exp_expr(m_exp.group(1)) if m_exp else (0, 0)
                skills = {s: int(v) for s, v in _RE_SKILL.findall(content)}
                applies = {k: int(v) for k, v in _RE_APPLY.findall(content)}
                dodge = skills.get("dodge", 0) + applies.get("dodge", 0) + applies.get("defense", 0)
                attack = max((v for s, v in skills.items() if s in _ATTACK_SKILLS),
                             default=0) + applies.get("attack", 0)
                rel = os.path.relpath(fpath, MUDLIB)[:-2]
                m_att = _RE_ATTITUDE.search(content)
                attitude = m_att.group(1) if m_att else ""
                npcs[rel] = {
                    "file": rel,
                    "cn": cn,
                    "ids": [i.lower() for i in ids],
                    "exp_min": exp_min,
                    "exp_max": exp_max,
                    "skills": skills,
                    "dp_est": power_est(dodge, exp_min),
                    "ap_est": power_est(attack, exp_min),
                    "armed": bool(_RE_WIELD.search(content)),
                    "beast": bool(_RE_RACE_BEAST.search(content)),
                    "attitude": attitude,
                    "aggressive": attitude in ("aggressive", "killer"),
                }
    return npcs


def parse_qlists() -> dict:
    """qlist 目标：中文名 → [{tier, time, exp_bonus, pot_bonus}]"""
    targets = {}
    qdir = os.path.join(MUDLIB, "quest")
    re_entry = re.compile(
        r'\(\[\s*"quest"\s*:\s*"([^"]+)"\s*,\s*"quest_type"\s*:\s*"([^"]+)"\s*,'
        r'\s*"time"\s*:\s*(\d+)\s*,\s*"exp_bonus"\s*:\s*(\d+)\s*,'
        r'\s*"pot_bonus"\s*:\s*(\d+)', re.DOTALL)
    for fname in sorted(os.listdir(qdir)):
        m = re.match(r'qlist(\d+)\.c$', fname)
        if not m:
            continue
        tier = int(m.group(1))
        with open(os.path.join(qdir, fname), encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for cn, qtype, t, exp_b, pot_b in re_entry.findall(content):
            targets.setdefault(cn, []).append({
                "tier": tier, "type": qtype, "time": int(t),
                "exp_bonus": int(exp_b), "pot_bonus": int(pot_b),
            })
    return targets


def main():
    with open(config.WORLD_MAP_FILE, encoding="utf-8") as f:
        world = json.load(f)
    room_npcs = world.get("room_npcs", {})

    # npc 文件 → 出现房间列表
    npc_rooms = {}
    for room, objs in room_npcs.items():
        for obj_path, count in objs.items():
            npc_rooms.setdefault(obj_path, []).append({"room": room, "count": count})

    npcs = scan_npcs()
    print(f"扫描到 {len(npcs)} 个 NPC 文件")

    # 按中文名聚合
    by_cn = {}
    for rel, info in npcs.items():
        info["rooms"] = npc_rooms.get(rel, [])
        by_cn.setdefault(info["cn"], []).append(info)

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.NPC_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(by_cn, f, ensure_ascii=False, indent=1)
    located = sum(1 for entries in by_cn.values() if any(e["rooms"] for e in entries))
    print(f"已写入 {config.NPC_INDEX_FILE}（{len(by_cn)} 个名字，{located} 个可定位）")

    # ---- 陪练阶梯（kill 模式，不走 accept_fight）：只排除持武器（真伤）的 ----
    # 野兽优势：cps 5~15（人类 10~30）→ 我方出手频率约 2 倍；不说话不浪费心跳；
    # bite/claw 伤害固定低。friendly 也能 kill。
    ladder = []
    for cn, entries in by_cn.items():
        for e in entries:
            if e["armed"]:
                continue
            if not e["rooms"]:
                continue
            ladder.append({
                "cn": cn, "id": e["ids"][0] if e["ids"] else "",
                "file": e["file"], "rooms": e["rooms"],
                "exp_min": e["exp_min"], "dp_est": e["dp_est"], "ap_est": e["ap_est"],
                "skills": e["skills"], "attitude": e.get("attitude", ""),
            })
    ladder.sort(key=lambda x: x["dp_est"])
    with open(config.SPAR_LADDER_FILE, "w", encoding="utf-8") as f:
        json.dump(ladder, f, ensure_ascii=False, indent=1)
    print(f"已写入 {config.SPAR_LADDER_FILE}（{len(ladder)} 个候选陪练）")
    print("  阶梯前 12 档：")
    for e in ladder[:12]:
        print(f"    dp≈{e['dp_est']:>8}  {e['cn']}({e['id']})  exp={e['exp_min']}  房间={e['rooms'][0]['room']}")

    # ---- 任务白名单 ----
    qtargets = parse_qlists()
    whitelist = []
    missing = []
    for cn, quests in qtargets.items():
        entries = by_cn.get(cn, [])
        locatable = [e for e in entries if e["rooms"]]
        if not locatable:
            missing.append(cn)
            continue
        e = locatable[0]
        whitelist.append({
            "target_cn": cn,
            "kill_id": e["ids"][0] if e["ids"] else "",
            "rooms": [r["room"] for r in e["rooms"]],
            "exp_min": e["exp_min"], "exp_max": e["exp_max"],
            "dp_est": e["dp_est"], "ap_est": e["ap_est"],
            "armed": e["armed"], "beast": e["beast"], "aggressive": e["aggressive"],
            "quests": quests,
        })
    with open(config.QUEST_WHITELIST_FILE, "w", encoding="utf-8") as f:
        json.dump(whitelist, f, ensure_ascii=False, indent=1)
    all_targets = len(qtargets)
    print(f"已写入 {config.QUEST_WHITELIST_FILE}：{len(whitelist)}/{all_targets} 个任务目标可定位")
    if missing:
        print(f"  不可定位目标（领到将等超时跳过）: {'、'.join(missing[:20])}{' ...' if len(missing) > 20 else ''}")


if __name__ == "__main__":
    main()
