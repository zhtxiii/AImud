"""
世界模型：加载离线生成的 world_map.json / npc_index.json，
提供 BFS 寻路、房名定位、关键锚点与危险区策略。
"""
import json
from collections import deque

import config

# 关键地点锚（mudlib 相对路径，不带 .c）
ANCHORS = {
    "inn": "d/snow/inn",                 # 出生点 饮风客栈
    "square": "d/snow/square",           # 广场
    "school2": "d/snow/school2",         # 武场（trainee/李火狮）
    "schoolhall": "d/snow/schoolhall",   # 武馆大厅（柳淳风）
    "god2": "u/cloud/god2",              # 朱鸿雪（任务NPC）
    "herbshop": "d/snow/herbshop",       # 药铺
    "death_gate": "d/death/gate",        # 鬼门关
    "death_inn1": "d/death/inn1",        # 复活客栈
    "snow_temple": "d/snow/temple",      # 雪山寺（复活落点）
}

# 危险房间策略：
#   run_through —— 允许通过但不可停留（主动攻击 NPC），导航用连发协议
#   forbidden   —— 寻路绝对禁行
DANGER = {
    "u/cloud/dragonhill/hummock": "run_through",  # 卧龙岗 2×持刀 Gangster
}
FORBIDDEN_PREFIXES = ("d/wiz",)  # 审判官/法庭区域


class World:
    def __init__(self, map_path: str = None, npc_index_path: str = None):
        map_path = map_path or config.WORLD_MAP_FILE
        npc_index_path = npc_index_path or config.NPC_INDEX_FILE
        with open(map_path, encoding="utf-8") as f:
            data = json.load(f)
        self.nodes: dict = data["nodes"]            # path -> {label}
        self.room_npcs: dict = data.get("room_npcs", {})
        self.adj: dict = {}
        for e in data["edges"]:
            self.adj.setdefault(e["from"], []).append((e["dir"], e["to"]))
        self.label_to_paths: dict = {}
        for path, info in self.nodes.items():
            self.label_to_paths.setdefault(info["label"], []).append(path)
        try:
            with open(npc_index_path, encoding="utf-8") as f:
                self.npc_index: dict = json.load(f)
        except FileNotFoundError:
            self.npc_index = {}

    # ------------------------------------------------------------------
    def label_of(self, path: str) -> str:
        info = self.nodes.get(path)
        return info["label"] if info else path

    def is_forbidden(self, path: str) -> bool:
        return path.startswith(FORBIDDEN_PREFIXES)

    def danger_of(self, path: str) -> str | None:
        return DANGER.get(path)

    # ------------------------------------------------------------------
    def find_path(self, src: str, dst: str) -> list[dict] | None:
        """
        BFS 最短路。返回步骤列表 [{dir, to, label, danger}]，不可达返回 None。
        禁行房间绝不进入；run_through 房间加权惩罚（优先绕路，无路可绕才穿越）。
        """
        if src == dst:
            return []
        if src not in self.nodes:
            return None
        # 两轮 BFS：先避开 run_through，失败再允许
        for allow_danger in (False, True):
            path = self._bfs(src, dst, allow_danger)
            if path is not None:
                return path
        return None

    def _bfs(self, src: str, dst: str, allow_danger: bool) -> list[dict] | None:
        queue = deque([(src, [])])
        seen = {src}
        while queue:
            node, steps = queue.popleft()
            for direction, nxt in self.adj.get(node, []):
                if nxt in seen or self.is_forbidden(nxt):
                    continue
                if not allow_danger and self.danger_of(nxt) and nxt != dst:
                    continue
                step = {"dir": direction, "to": nxt,
                        "label": self.label_of(nxt),
                        "danger": self.danger_of(nxt)}
                if nxt == dst:
                    return steps + [step]
                seen.add(nxt)
                queue.append((nxt, steps + [step]))
        return None

    # ------------------------------------------------------------------
    def locate_by_label(self, label: str, near: str = None, max_hops: int = 3) -> str | None:
        """
        房名 → 路径。重名时用 near（最近已知位置）在 max_hops 跳内消歧。
        """
        candidates = self.label_to_paths.get(label.strip(), [])
        if not candidates:
            return None
        if len(candidates) == 1 or not near:
            return candidates[0]
        # 邻近性消歧：从 near 做有限 BFS
        queue = deque([(near, 0)])
        seen = {near}
        while queue:
            node, hops = queue.popleft()
            if node in candidates:
                return node
            if hops >= max_hops:
                continue
            for _d, nxt in self.adj.get(node, []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, hops + 1))
        return candidates[0]

    # ------------------------------------------------------------------
    def neighbors(self, path: str) -> list[tuple[str, str]]:
        return self.adj.get(path, [])

    def steps_between(self, src: str, dst: str) -> int:
        p = self.find_path(src, dst)
        return len(p) if p is not None else -1

    def npc_sites(self, cn_name: str) -> list[dict]:
        """中文名 → NPC 索引条目（含 rooms）。"""
        return self.npc_index.get(cn_name, [])


_world_singleton = None


def get_world() -> World:
    global _world_singleton
    if _world_singleton is None:
        _world_singleton = World()
    return _world_singleton
