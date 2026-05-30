"""
Task6 配置模块
从 apikey.txt 读取 API Key，支持环境变量覆盖。
"""
import os

# --- 读取 API Key ---
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APIKEY_FILE = os.path.join(_SCRIPT_DIR, "apikey.txt")

def _load_api_key():
    """从 apikey.txt 读取 API Key"""
    try:
        with open(_APIKEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

# --- Socket 连接配置 ---
TARGET_IP = os.environ.get("AGENT_TARGET_IP", "127.0.0.1")
TARGET_PORT = int(os.environ.get("AGENT_TARGET_PORT", 4000))

# --- DeepSeek 配置 ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or _load_api_key()
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

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
