"""测试隔离：把持久化目录重定向到 /tmp，避免污染生产 checkpoint。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="agent_test_")
import config
config.CHECKPOINT_FILE = os.path.join(_tmp, "checkpoint.json")
config.CREDENTIALS_FILE = os.path.join(_tmp, "credentials.json")
config.PROGRESS_CSV = os.path.join(_tmp, "progress.csv")
config.DEATHS_LOG = os.path.join(_tmp, "deaths.log")
