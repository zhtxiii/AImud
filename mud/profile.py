"""
ES2 MUD 文本协议知识：解析器、事件检测、命令构造。
全部为纯函数 + 预编译正则，零 IO。
所有格式均核验自 mudlib 源码（cmds/usr/hp.c, score.c, cmds/std/look.c,
adm/daemons/combatd.c, logind.c, u/cloud/npc/god.c, feature/more.c 等）。
"""
import re

# ============================================================
#  中文数字
# ============================================================

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def cn2int(s: str) -> int:
    """中文数字 → int，如 二十三 → 23，一百零五 → 105。纯数字串直接转。"""
    s = s.strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                total = (total + section + (num if num else 0)) * 10000
                section, num = 0, 0
            else:
                section += (num if num else 1) * unit
                num = 0
    return total + section + num


_DURATION_RE = re.compile(r"(?:([零〇一二两三四五六七八九十百千万\d]+)天)?"
                          r"(?:([零〇一二两三四五六七八九十百千万\d]+)小时)?"
                          r"(?:([零〇一二两三四五六七八九十百千万\d]+)分)?"
                          r"(?:([零〇一二两三四五六七八九十百千万\d]+)秒)?")


def duration_cn(s: str) -> int:
    """中文时长 → 秒，如 三分二十秒 → 200。"""
    m = _DURATION_RE.search(s)
    if not m:
        return 0
    d, h, mi, sec = (cn2int(g) if g else 0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


# ============================================================
#  ANSI 清理
# ============================================================

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    return "".join(ch for ch in text if ch == "\n" or (ord(ch) >= 32 and ord(ch) != 127))


_RE_NOISE_LINE = re.compile(
    r"编译时段错误|Warning: .*before the end of line|"
    r"Warning: Expression has no side effects|^\s*\^\s*$|VF\*Z")


def strip_noise(text: str) -> str:
    """过滤 mudlib 编译警告等与游戏无关的噪声行。"""
    return "\n".join(l for l in text.splitlines() if not _RE_NOISE_LINE.search(l))


# ============================================================
#  状态解析：hp / score
# ============================================================

_HP_TRIPLE = r"：\s*(\d+)/\s*(\d+)\s*\(\s*(\d+)%\)"
_RE_GIN = re.compile("精" + _HP_TRIPLE)
_RE_KEE = re.compile("气" + _HP_TRIPLE)
_RE_SEN = re.compile("神" + _HP_TRIPLE)
_RE_FORCE = re.compile(r"内力：\s*(\d+)/\s*(\d+)")
_RE_FOOD = re.compile(r"食物：\s*(\d+)/\s*(\d+)")
_RE_WATER = re.compile(r"饮水：\s*(\d+)/\s*(\d+)")
_RE_POT_HP = re.compile(r"潜能：\s*(\d+)")
_RE_EXP_HP = re.compile(r"经验：\s*(\d+)")


def parse_hp(text: str) -> dict | None:
    """
    解析 hp 命令输出。返回 None 表示文本中没有 hp 输出。
    注意：hp 显示的百分比是 eff/max（伤势完好度），当前余量百分比需自行计算
    kee_pct = kee*100/eff_kee。
    """
    m_kee = _RE_KEE.search(text)
    if not m_kee:
        return None
    result = {}
    for key, regex in (("gin", _RE_GIN), ("kee", _RE_KEE), ("sen", _RE_SEN)):
        m = regex.search(text)
        if m:
            cur, eff, integrity = int(m.group(1)), int(m.group(2)), int(m.group(3))
            result[key] = cur
            result[f"eff_{key}"] = eff
            result[f"{key}_integrity"] = integrity  # eff/max %，<100 即有 wound
            result[f"{key}_pct"] = cur * 100 // eff if eff > 0 else 0
    for key, regex in (("force", _RE_FORCE), ("food", _RE_FOOD), ("water", _RE_WATER)):
        m = regex.search(text)
        if m:
            result[key] = int(m.group(1))
            result[f"max_{key}"] = int(m.group(2))
    m = _RE_POT_HP.search(text)
    if m:
        result["potential"] = int(m.group(1))  # 可用潜能 = potential - learned_points
    m = _RE_EXP_HP.search(text)
    if m:
        result["exp"] = int(m.group(1))
    result["wounded"] = (result.get("kee_integrity", 100) < 100
                         or result.get("gin_integrity", 100) < 100
                         or result.get("sen_integrity", 100) < 100)
    return result


_RE_EXP_SCORE = re.compile(r"实战经验：\s*(\d+)")
_RE_POT_SCORE = re.compile(r"潜\s*能：\s*(\d+)")
_RE_SCORE_VAL = re.compile(r"综合评价：\s*(\d+)")
_RE_BELL = re.compile(r"杀\s*气：\s*(\d+)")
_RE_AP = re.compile(r"攻击力：\s*(\d+)")
_RE_DP = re.compile(r"防御力：\s*(\d+)")


def parse_score(text: str) -> dict | None:
    """解析 score 命令输出。ap/dp = (攻击力/防御力)×100（skill_power 近似还原）。"""
    m = _RE_EXP_SCORE.search(text)
    if not m:
        return None
    result = {"exp": int(m.group(1))}
    for key, regex in (("potential", _RE_POT_SCORE), ("score", _RE_SCORE_VAL),
                       ("bellicosity", _RE_BELL)):
        mm = regex.search(text)
        if mm:
            result[key] = int(mm.group(1))
    m_ap = _RE_AP.search(text)
    m_dp = _RE_DP.search(text)
    # score.c: 显示值 = skill_power/100 + 1 → 还原下界 (val-1)*100
    if m_ap:
        result["ap"] = max(0, (int(m_ap.group(1)) - 1)) * 100
    if m_dp:
        result["dp"] = max(0, (int(m_dp.group(1)) - 1)) * 100
    return result


# ============================================================
#  房间解析（look 输出）
# ============================================================

_RE_ROOM_TITLE = re.compile(r"▲\s*(.+)")
_RE_EXIT_ONE = re.compile(r"这里唯一的出口是\s*(\S+?)。")
_RE_EXIT_MANY = re.compile(r"这里明显的出口是\s*(.+?)。")
_RE_NO_EXIT = re.compile(r"这里没有任何明显的出路")
# NPC/物品 short 常见格式：中文名(Eng Id)
_RE_OBJ_LINE = re.compile(r"^\s{2,}(\S[^\n]*?)\s*$")
_RE_OBJ_ID = re.compile(r"(.+?)\(([A-Za-z][A-Za-z' \-]*)\)\s*$")


def parse_room(text: str) -> dict | None:
    """
    解析 look 输出。返回 {name, exits[], objects[{short,cn,id}]}，无房间标记返回 None。
    容忍战斗噪声：只取最后一个 ▲ 块。
    """
    titles = list(_RE_ROOM_TITLE.finditer(text))
    if not titles:
        return None
    start = titles[-1].start()
    block = text[start:]
    name = titles[-1].group(1).strip()
    # 房名可能带后缀装饰（如 雪亭镇广场(Square)），保留原样并另存纯中文部分
    exits = []
    m_one = _RE_EXIT_ONE.search(block)
    m_many = _RE_EXIT_MANY.search(block)
    if m_one:
        exits = [m_one.group(1).strip()]
    elif m_many:
        raw = m_many.group(1)
        parts = re.split(r"、|和", raw)
        exits = [p.strip() for p in parts if p.strip()]
    objects = []
    exits_line_seen = False
    for line in block.splitlines()[1:]:
        if _RE_EXIT_ONE.search(line) or _RE_EXIT_MANY.search(line) or _RE_NO_EXIT.search(line):
            exits_line_seen = True
            continue
        if not exits_line_seen:
            continue  # 出口行之前是房间长描述
        m = _RE_OBJ_LINE.match(line)
        if not m:
            if line.strip() == "":
                continue
            break  # 缩进块结束
        short = m.group(1).strip()
        mid = _RE_OBJ_ID.match(short)
        if mid:
            objects.append({"short": short, "cn": mid.group(1).strip(),
                            "id": mid.group(2).strip().lower()})
        else:
            objects.append({"short": short, "cn": short, "id": ""})
    return {"name": name, "exits": exits, "objects": objects}


# ============================================================
#  任务解析（朱鸿雪）
# ============================================================

_RE_QUEST_TIME = re.compile(r"请在(.+?)内")
_RE_QUEST_KILL = re.compile(r"替我杀了『(.+?)』")
_RE_QUEST_FETCH = re.compile(r"找回『(.+?)』给我")
_RE_QUEST_DONE = re.compile(r"恭喜你！你又完成了一项任务")
_RE_QUEST_LATE = re.compile(r"真可惜！你没有在指定的时间内完成")
_RE_REWARD_EXP = re.compile(r"([零〇一二两三四五六七八九十百千万\d]+)点实战经验")
_RE_REWARD_POT = re.compile(r"([零〇一二两三四五六七八九十百千万\d]+)点潜能")


def parse_quest_grant(text: str) -> dict | None:
    """解析领取任务的回应。返回 {type:'kill'|'fetch', target_cn, limit_sec} 或 None。"""
    m_kill = _RE_QUEST_KILL.search(text)
    m_fetch = _RE_QUEST_FETCH.search(text)
    if not m_kill and not m_fetch:
        return None
    m_time = _RE_QUEST_TIME.search(text)
    limit = duration_cn(m_time.group(1)) if m_time else 0
    if m_kill:
        return {"type": "kill", "target_cn": m_kill.group(1).strip(), "limit_sec": limit}
    return {"type": "fetch", "target_cn": m_fetch.group(1).strip(), "limit_sec": limit}


def parse_reward(text: str) -> dict | None:
    """解析任务奖励文本（中文数字）。"""
    m_exp = _RE_REWARD_EXP.search(text)
    if not m_exp:
        return None
    result = {"exp": cn2int(m_exp.group(1))}
    m_pot = _RE_REWARD_POT.search(text)
    if m_pot:
        result["potential"] = cn2int(m_pot.group(1))
    return result


# ============================================================
#  事件检测
# ============================================================

# (事件名, 预编译正则)。检测顺序即列表顺序。
_EVENT_PATTERNS = [
    # --- 登录序列 ---
    ("LOGIN_NAME", re.compile(r"您的英文名字：|请重新输入您的英文名字：")),
    ("LOGIN_NEW_CONFIRM", re.compile(r"将会创造一个新的人物，您确定吗\(y/n\)")),
    ("LOGIN_PASSWORD", re.compile(r"请输入密码：")),
    ("LOGIN_SET_PASSWORD", re.compile(r"请设定您的密码：")),
    ("LOGIN_CONFIRM_PASSWORD", re.compile(r"请再输入一次您的密码")),
    ("LOGIN_CN_NAME", re.compile(r"您的中文名字：")),
    ("LOGIN_EMAIL", re.compile(r"您的电子邮件地址：")),
    ("LOGIN_GENDER", re.compile(r"您要扮演男性\(m\)的角色或女性\(f\)的角色|您只能选择男性\(m\)或女性\(f\)")),
    ("LOGIN_TAKEOVER", re.compile(r"您要将另一个连线中的相同人物赶出去")),
    ("PASSWORD_ERROR", re.compile(r"密码错误！")),
    # --- 分页 ---
    ("PAGER", re.compile(r"==\s*未完继续\s*\d+%\s*==")),
    # --- 任务 ---
    ("QUEST_DONE", _RE_QUEST_DONE),
    ("QUEST_LATE", _RE_QUEST_LATE),
    ("QUEST_GRANTED", re.compile(r"朱鸿雪沉思了一会儿")),
    ("REWARD", re.compile(r"你被奖励了：")),
    # --- 战斗与生死 ---
    ("SKILL_IMPROVED", re.compile(r"你的「(.+?)」进步了")),
    ("SELF_DEATH", re.compile(r"^你死了。", re.M)),
    ("SELF_UNCONSCIOUS", re.compile(r"你的眼前一黑，接著什麽也不知道了")),
    ("SELF_REVIVE", re.compile(r"慢慢地你终於又有了知觉")),
    ("SPAR_END", re.compile(r"承让|佩服，佩服|这场比试算我输了|阁下武艺不凡")),
    ("SPAR_START", re.compile(r"领教.{0,8}高招|在下只好奉陪|赐教")),
    ("OPPONENT_DOWN", re.compile(r"跌在地上一动也不动了|已经无法战斗了")),
    ("SOMEONE_DIED", re.compile(r"(\S+)死了。")),
    ("FIGHT_REFUSED", re.compile(r"看起来(.+?)并不想跟你较量")),
    ("NO_FIGHT_ROOM", re.compile(r"这里禁止战斗|此地禁止|不准战斗")),
    ("SELF_SURRENDER", re.compile(r"不打了，不打了，我投降")),
    ("SURRENDER_REFUSED", re.compile(r"废话少说，纳命来")),
    ("SELF_FLEE", re.compile(r"你慌里慌张往(\S+?)\((\w+)\)逃去")),
    ("NO_SUCH_TARGET", re.compile(r"这里没有这个人|你要杀谁|你要跟谁比试|你的攻击目标|你想攻击谁")),
    ("DOOR_CLOSED", re.compile(r"这个(\S+?)是关着的。你可以打开|你必须先把(\S+?)打开")),
    ("NO_EXIT", re.compile(r"这个方向没有出路|那个方向没有出路")),
    ("CONFUSED_CMD", re.compile(r"^什么？$", re.M)),
    ("BUSY_FIGHTING", re.compile(r"你正在战斗中|战斗中无法")),
    ("TOO_TIRED_LEARN", re.compile(r"你今天太累了，结果什麽也没有学到")),
    ("LEARN_NO_POT", re.compile(r"你的潜能已经发挥到极限")),
    ("LEARN_CANNOT", re.compile(r"依你目前的能力，没有办法学习|不愿意教你这项技能|这项技能你恐怕必须找别人学|程度已经不输你师父|你要向谁求教")),
    ("NO_MONEY", re.compile(r"钱不够|没有这么多钱|付不起|身上没有足够")),
    ("QUEST_HAVE", re.compile(r"你现在的任务是(杀|寻)『(.+?)』")),
    ("QUEST_NONE", re.compile(r"你现在没有任何任务")),
    ("QUEST_NOTIME", re.compile(r"你已经没有足够的时间来完成它了")),
    ("QUEST_TIME_LEFT", re.compile(r"你还有(.+?)去完成它")),
    ("GHOST_HINT", re.compile(r"鬼门关|阴风惨惨|你已经死了")),
    # --- 巫师反机器人 ---
    ("ROBOT_CHECK", re.compile(r"审判官|回答这个问题|answer\s*<")),
]


def detect_events(text: str) -> list[dict]:
    """扫描文本返回事件列表 [{type, match, groups}]，按出现位置排序。"""
    events = []
    for name, pattern in _EVENT_PATTERNS:
        for m in pattern.finditer(text):
            events.append({"type": name, "pos": m.start(),
                           "match": m.group(0), "groups": m.groups()})
    events.sort(key=lambda e: e["pos"])
    return events


def has_event(events: list[dict], *types: str) -> dict | None:
    """返回第一个匹配类型的事件。"""
    for e in events:
        if e["type"] in types:
            return e
    return None


# ============================================================
#  命令构造（防注入：id 只允许安全字符）
# ============================================================

_SAFE_ID_RE = re.compile(r"^[a-z][a-z' \-]{0,30}$")
_SAFE_DIR_RE = re.compile(r"^[a-z]{1,12}$")


def _safe_id(target_id: str) -> str:
    t = (target_id or "").strip().lower()
    if not _SAFE_ID_RE.match(t):
        raise ValueError(f"非法目标 id: {target_id!r}")
    return t


class cmd:
    """MUD 命令构造器。"""

    @staticmethod
    def look(target: str = "") -> str:
        return f"look {_safe_id(target)}" if target else "look"

    @staticmethod
    def go(direction: str) -> str:
        d = direction.strip().lower()
        if not _SAFE_DIR_RE.match(d):
            raise ValueError(f"非法方向: {direction!r}")
        return d

    @staticmethod
    def fight(target_id: str, n: int = 0) -> str:
        base = f"fight {_safe_id(target_id)}"
        return f"{base} {n}" if n >= 2 else base

    @staticmethod
    def kill(target_id: str, n: int = 0) -> str:
        base = f"kill {_safe_id(target_id)}"
        return f"{base} {n}" if n >= 2 else base

    @staticmethod
    def surrender() -> str:
        return "surrender"

    @staticmethod
    def hp() -> str:
        return "hp"

    @staticmethod
    def score() -> str:
        return "score"

    @staticmethod
    def save() -> str:
        return "save"

    @staticmethod
    def quest() -> str:
        return "quest"

    @staticmethod
    def set_wimpy(pct: int) -> str:
        return f"set wimpy {int(pct)}"

    @staticmethod
    def learn(skill: str, master_id: str) -> str:
        s = skill.strip().lower()
        if not re.match(r"^[a-z][a-z\-]{0,30}$", s):
            raise ValueError(f"非法技能名: {skill!r}")
        return f"learn {s} from {_safe_id(master_id)}"

    @staticmethod
    def apprentice(master_id: str) -> str:
        return f"apprentice {_safe_id(master_id)}"

    @staticmethod
    def buy(item: str, vendor_id: str = "") -> str:
        i = item.strip().lower()
        if not re.match(r"^[a-z][a-z' \-]{0,30}$", i):
            raise ValueError(f"非法物品名: {item!r}")
        if vendor_id:
            return f"buy {i} from {_safe_id(vendor_id)}"
        return f"buy {i}"

    @staticmethod
    def eat(item: str) -> str:
        return f"eat {_safe_id(item)}"

    @staticmethod
    def drink(item: str) -> str:
        return f"drink {_safe_id(item)}"

    @staticmethod
    def get(item: str, source: str = "") -> str:
        i = _safe_id(item)
        return f"get {i} from {_safe_id(source)}" if source else f"get {i}"

    @staticmethod
    def give(amount: int, item: str, target: str) -> str:
        return f"give {int(amount)} {_safe_id(item)} to {_safe_id(target)}"

    @staticmethod
    def study(item: str) -> str:
        return f"study {_safe_id(item)}"

    @staticmethod
    def exercise(kee: int) -> str:
        return f"exercise {int(kee)}"

    @staticmethod
    def wield(item: str) -> str:
        return f"wield {_safe_id(item)}"

    @staticmethod
    def apply_medicine() -> str:
        return "apply medicine"

    @staticmethod
    def exert(what: str = "recover") -> str:
        w = what.strip().lower()
        if not re.match(r"^[a-z]{1,20}$", w):
            raise ValueError(f"非法 exert: {what!r}")
        return f"exert {w}"

    @staticmethod
    def enable(usage: str, skill: str) -> str:
        for s in (usage, skill):
            if not re.match(r"^[a-z][a-z\-]{0,30}$", s.strip().lower()):
                raise ValueError(f"非法 enable 参数: {usage!r} {skill!r}")
        return f"enable {usage.strip().lower()} {skill.strip().lower()}"

    @staticmethod
    def ask(target_id: str, topic: str) -> str:
        t = topic.strip()
        if re.search(r"[\r\n;|]", t) or len(t) > 30:
            raise ValueError(f"非法话题: {topic!r}")
        return f"ask {_safe_id(target_id)} about {t}"

    @staticmethod
    def answer(number: int) -> str:
        return f"answer {int(number)}"
