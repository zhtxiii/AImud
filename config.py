"""
Task6 配置模块
支持多个 LLM 提供商（DeepSeek / Polo API），启动时可选。
从 apikey.txt 读取 DeepSeek API Key，从 poloapi.txt 读取 Polo API 配置，支持环境变量覆盖。
"""
import os

# --- 读取 API Key 和 Polo API 配置 ---
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APIKEY_FILE = os.path.join(_SCRIPT_DIR, "apikey.txt")
_POLOAPI_FILE = os.path.join(_SCRIPT_DIR, "poloapi.txt")

def _load_api_key():
    """从 apikey.txt 读取 API Key"""
    try:
        with open(_APIKEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def _load_polo_config():
    """从 poloapi.txt 读取 Polo API 配置（跳过空行，依次取 key / model / base_url）"""
    try:
        with open(_POLOAPI_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
            api_key = lines[0] if len(lines) > 0 else ""
            model = lines[1] if len(lines) > 1 else "claude-fable-5"
            base_url = lines[2] if len(lines) > 2 else "https://poloapi.top/v1"
            return api_key, model, base_url
    except FileNotFoundError:
        return "", "claude-fable-5", "https://poloapi.top/v1"

# --- Socket 连接配置 ---
TARGET_IP = os.environ.get("AGENT_TARGET_IP", "127.0.0.1")
TARGET_PORT = int(os.environ.get("AGENT_TARGET_PORT", 4000))

# --- DeepSeek 配置 ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or _load_api_key()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# --- Polo API 配置（兼容 OpenAI 格式） ---
POLO_API_KEY, POLO_MODEL, POLO_BASE_URL = _load_polo_config()
# 也支持环境变量覆盖
POLO_API_KEY = os.environ.get("POLO_API_KEY") or POLO_API_KEY
POLO_MODEL = os.environ.get("POLO_MODEL") or POLO_MODEL
POLO_BASE_URL = os.environ.get("POLO_BASE_URL") or POLO_BASE_URL

# --- 当前激活的提供商（由 select_model() 设置） ---
ACTIVE_PROVIDER = "deepseek"  # "deepseek" 或 "polo"

# ---- 可用模型列表 ----
AVAILABLE_MODELS = {
    "1": {
        "name": "DeepSeek",
        "provider": "deepseek",
        "api_key": DEEPSEEK_API_KEY,
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
    },
    "2": {
        "name": f"Polo API ({POLO_MODEL})",
        "provider": "polo",
        "api_key": POLO_API_KEY,
        "model": POLO_MODEL,
        "base_url": POLO_BASE_URL,
    },
}


def select_model():
    """
    选择 LLM 模型，在智能体启动时调用。
    优先读取 AGENT_MODEL 环境变量（1=DeepSeek, 2=Polo），便于无人值守启动；
    未设置时交互式选择。
    返回所选模型的配置字典。
    """
    env_choice = os.environ.get("AGENT_MODEL", "").strip()
    if env_choice in AVAILABLE_MODELS:
        selected = AVAILABLE_MODELS[env_choice]
        globals()["ACTIVE_PROVIDER"] = selected["provider"]
        print(f"\n  ✅ [AGENT_MODEL={env_choice}] 已选择: {selected['name']}\n")
        return selected
    if env_choice:
        print(f"  ⚠️ AGENT_MODEL={env_choice} 无效，回退到交互选择。")

    print("\n" + "=" * 50)
    print("  请选择要使用的 LLM 模型：")
    print("=" * 50)

    for key, m in AVAILABLE_MODELS.items():
        print(f"  [{key}] {m['name']}")
        status = "✅ 已配置" if m["api_key"] else "❌ 未配置 API Key"
        print(f"      模型: {m['model']}  {status}")
        print(f"      地址: {m['base_url']}")
        print()

    while True:
        choice = input("  请输入编号 (1/2)：").strip()
        if choice in AVAILABLE_MODELS:
            selected = AVAILABLE_MODELS[choice]
            globals()["ACTIVE_PROVIDER"] = selected["provider"]
            print(f"\n  ✅ 已选择: {selected['name']}\n")
            return selected
        print("  ⚠️ 无效选择，请输入 1 或 2。")

# --- 智能体运行配置 ---
MAX_HISTORY_ROUNDS = 50
LOG_DIR = os.path.join(_SCRIPT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "system", "interaction.log")
PLANNER_LOG_FILE = os.path.join(LOG_DIR, "planner", "history.log")
KNOWLEDGE_LOG_FILE = os.path.join(LOG_DIR, "knowledge", "manager.log")
TASK_LOG_DIR = os.path.join(LOG_DIR, "tasks")
REFLECTOR_LOG_FILE = os.path.join(LOG_DIR, "reflector", "reflections.log")
DATA_DIR = os.path.join(_SCRIPT_DIR, "data")
REFLECTIONS_DIR = os.path.join(DATA_DIR, "reflections")
EXPERIENCES_FILE = os.path.join(REFLECTIONS_DIR, "experiences.json")
KB_FILE = os.path.join(DATA_DIR, "knowledge_base.json")  # 保留兼容
KB_DIR = os.path.join(DATA_DIR, "knowledge_bases")  # 阶段化知识库目录
KB_CONSOLIDATION_INTERVAL = 20  # 每隔 N 轮整理一次知识库
MAX_TASK_ATTEMPTS = 50           # 单个任务最大尝试轮数，超过则判定为僵局

# --- LLM 可靠性 ---
LLM_CALL_TIMEOUT = float(os.environ.get("LLM_CALL_TIMEOUT", 120.0))  # 单次调用超时（秒）
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", 5))          # call_with_retry 最大尝试次数

# --- 运行模式 ---
# grind: 里程碑驱动刷经验模式（本项目目标：10万实战经验）
# explore: 原有开放式探索模式
AGENT_MODE = os.environ.get("AGENT_MODE", "grind")
AUTO_START_MUD = os.environ.get("AUTO_START_MUD", "1") == "1"
MUD_PROJECT_DIR = os.environ.get("MUD_PROJECT_DIR", os.path.expanduser("~/project"))

# --- grind 模式参数 ---
GOAL_EXP = int(os.environ.get("GOAL_EXP", 100000))   # 最终目标实战经验
KEE_DISENGAGE_PCT = 40       # 气低于该百分比 → 脱战（surrender）
KEE_RESUME_PCT = 75          # 气恢复到该百分比 → 重新接战
PARALLEL_SPAR = 3            # 同时切磋的对手数（多对手=多倍经验事件）
ENGAGE_SWEEP_SEC = 3.0       # 多目标重新接战扫描间隔（秒）
KEE_ABORT_KILL_PCT = 35      # 真打中气低于该百分比 → 撤退弃单
WIMPY_PCT = 40               # set wimpy 兜底自动逃跑阈值（kill 陪练模式需要高阈值防猝死）
SPAR_HP_POLL_SEC = 3         # kill 陪练模式 hp 轮询间隔（秒）
SPAR_BLACKLIST_TTL = 1200    # 驻点拉黑过期时间（秒）≈ 1.5 个 reset 周期
FOOD_FLOOR = 40              # food 低于该值 → 进食
WATER_FLOOR = 40             # water 低于该值 → 饮水
SCORE_POLL_SEC = 90          # 切磋中 score 轮询间隔（秒）
CHECKPOINT_SEC = 60          # 例程内部 checkpoint 间隔（秒）
STALL_SOFT_MIN = 30          # 经验无增长 N 分钟 → 例程自我升级
STALL_HARD_MIN = 60          # 经验无增长 N 分钟 → fatal 交 watchdog 重启
MAX_REPAIR_ATTEMPTS = 15     # 升级修复任务（LLM 环路）的轮数预算
ROUTINE_LOOK_RETRY = 3       # 例程内定位/寻找类操作的重试次数

# --- 持久化 ---
CHECKPOINT_FILE = os.path.join(DATA_DIR, "checkpoint.json")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
PROGRESS_CSV = os.path.join(LOG_DIR, "system", "progress.csv")
DEATHS_LOG = os.path.join(LOG_DIR, "system", "deaths.log")
WORLD_MAP_FILE = os.path.join(DATA_DIR, "world_map.json")
NPC_INDEX_FILE = os.path.join(DATA_DIR, "npc_index.json")
SPAR_LADDER_FILE = os.path.join(DATA_DIR, "spar_ladder.json")
QUEST_WHITELIST_FILE = os.path.join(DATA_DIR, "quest_whitelist.json")

# --- 颜色配置 ---
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"   # Client
    YELLOW = "\033[93m"  # Short-term Goal
    BLUE = "\033[94m"    # Long-term Goal
    MAGENTA = "\033[95m" # KB Update
    CYAN = "\033[96m"    # Analysis
    WHITE = "\033[97m"
