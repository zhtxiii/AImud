#!/usr/bin/env python3
"""
离线生成世界地图：扫描 mudlib 的 d/ 和 u/ 全部 ROOM 文件，
输出有向图 data/world_map.json：
  {"nodes": {path: {"label": 房名}}, "edges": [{"from": path, "to": path, "dir": 方向}]}
并做 BFS 连通性自检（出生点→任务NPC/武馆/死亡区）。

只读 mudlib，不修改任何 MUD 文件。
运行：python3 tools/build_world.py
"""
import json
import os
import re
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

MUDLIB = os.path.join(config.MUD_PROJECT_DIR, "mudlib")
SEARCH_DIRS = [os.path.join(MUDLIB, "d"), os.path.join(MUDLIB, "u")]
OUTPUT = config.WORLD_MAP_FILE

_RE_INHERIT_ROOM = re.compile(r'inherit\s+(ROOM|"/std/room")\s*;')
_RE_SHORT = re.compile(r'set\("short"\s*,\s*"([^"]+)"')
_RE_EXITS_BLOCK = re.compile(r'set\("exits"\s*,\s*\(\[(.*?)\]\)\s*\)', re.DOTALL)
_RE_EXIT_PAIR = re.compile(r'"([a-z]+)"\s*:\s*([^,\n]+)')
_RE_OBJECTS_BLOCK = re.compile(r'set\("objects"\s*,\s*\(\[(.*?)\]\)\s*\)', re.DOTALL)
_RE_OBJECT_PAIR = re.compile(r'(__DIR__"[^"]+"|"[^"]+")\s*:\s*(\d+)')


def resolve_path(current_file: str, expr: str) -> str | None:
    """LPC 路径表达式 → mudlib 相对路径（不带 .c）。无法解析返回 None。"""
    expr = expr.strip().rstrip(",").strip()
    if "//" in expr.split('"')[0]:  # 整行被注释
        return None
    cur_dir = os.path.dirname(current_file)
    if "__DIR__" in expr:
        remainder = expr.replace("__DIR__", "").replace('"', "").replace("+", "").strip()
        path = os.path.join(cur_dir, remainder)
    else:
        clean = expr.replace('"', "").strip()
        if not clean or "(" in clean or ":" in clean:
            return None  # 函数指针/复杂表达式
        if clean.startswith("/"):
            path = os.path.join(MUDLIB, clean.lstrip("/"))
        else:
            path = os.path.join(cur_dir, clean)
    path = os.path.normpath(path)
    if path.endswith(".c"):
        path = path[:-2]
    rel = os.path.relpath(path, MUDLIB)
    if rel.startswith(".."):
        return None
    return rel


def scan() -> tuple[dict, list, dict]:
    rooms = {}     # rel_path(no .c) -> {"label": short}
    edges = []     # {"from","to","dir"}
    room_npcs = {} # rel_path -> {npc_rel_path: count}
    skipped = []

    for base in SEARCH_DIRS:
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
                if not _RE_INHERIT_ROOM.search(content):
                    continue
                m_short = _RE_SHORT.search(content)
                rel = os.path.relpath(fpath, MUDLIB)[:-2]
                if not m_short:
                    skipped.append(rel)
                    continue
                rooms[rel] = {"label": m_short.group(1)}

                m_exits = _RE_EXITS_BLOCK.search(content)
                if m_exits:
                    body = m_exits.group(1)
                    # 去掉注释行
                    body = "\n".join(l for l in body.splitlines()
                                     if not l.strip().startswith("//"))
                    for direction, dest in _RE_EXIT_PAIR.findall(body):
                        dest_rel = resolve_path(fpath, dest)
                        if dest_rel:
                            edges.append({"from": rel, "to": dest_rel, "dir": direction})

                m_objs = _RE_OBJECTS_BLOCK.search(content)
                if m_objs:
                    body = "\n".join(l for l in m_objs.group(1).splitlines()
                                     if not l.strip().startswith("//"))
                    npc_map = {}
                    for path_expr, count in _RE_OBJECT_PAIR.findall(body):
                        obj_rel = resolve_path(fpath, path_expr)
                        if obj_rel:
                            npc_map[obj_rel] = int(count)
                    if npc_map:
                        room_npcs[rel] = npc_map

    # 只保留终点是已知房间的边
    valid_edges = [e for e in edges if e["to"] in rooms and e["from"] in rooms]
    dropped = len(edges) - len(valid_edges)
    print(f"扫描完成：{len(rooms)} 个房间，{len(valid_edges)} 条有效边"
          f"（丢弃 {dropped} 条指向非房间/未解析目标的边，跳过 {len(skipped)} 个无short房间）")
    return rooms, valid_edges, room_npcs


def bfs(adj: dict, src: str, dst: str) -> list | None:
    if src not in adj:
        return None
    queue = deque([(src, [])])
    seen = {src}
    while queue:
        node, path = queue.popleft()
        if node == dst:
            return path
        for direction, nxt in adj.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [(direction, nxt)]))
    return None


def main():
    rooms, edges, room_npcs = scan()

    adj = {}
    for e in edges:
        adj.setdefault(e["from"], []).append((e["dir"], e["to"]))

    checks = [
        ("出生点→武场", "d/snow/inn", "d/snow/school2"),
        ("出生点→武馆大厅", "d/snow/inn", "d/snow/schoolhall"),
        ("出生点→任务NPC朱鸿雪", "d/snow/inn", "u/cloud/god2"),
        ("出生点→药铺", "d/snow/inn", "d/snow/herbshop"),
        ("鬼门关→复活客栈", "d/death/gate", "d/death/inn1"),
        ("雪山寺→出生点", "d/snow/temple", "d/snow/inn"),
    ]
    ok = True
    for name, src, dst in checks:
        path = bfs(adj, src, dst)
        if path is None:
            print(f"  [自检] ✗ {name}: {src} → {dst} 不可达！")
            ok = False
        else:
            dirs = "/".join(d for d, _ in path)
            print(f"  [自检] ✓ {name}: {len(path)} 步 ({dirs})")

    # 重名统计
    labels = {}
    for rel, info in rooms.items():
        labels.setdefault(info["label"], []).append(rel)
    dup = {k: v for k, v in labels.items() if len(v) > 1}
    print(f"  重名房间：{len(dup)} 组（导航需邻近性消歧）")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({"nodes": rooms, "edges": edges, "room_npcs": room_npcs},
                  f, ensure_ascii=False, indent=1)
    print(f"已写入 {OUTPUT}")
    if not ok:
        print("警告：关键路径自检未全部通过！")
        sys.exit(1)


if __name__ == "__main__":
    main()
