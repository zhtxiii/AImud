"""
持久化模块
checkpoint.json 原子读写（崩溃/重启后恢复任务进度）、credentials.json、progress.csv。
只持久化纯数据，client/llm/future 等对象字段一律过滤。
"""
import json
import os
import time

import config

CHECKPOINT_VERSION = 1

# state 中不可序列化或无需持久化的字段
_EXCLUDED_FIELDS = {
    "client", "llm", "kb_update_future",
    "server_output", "server_output_clean",
    "analysis", "action_type", "payload", "expected_result", "last_client_payload",
    "should_reconnect", "should_stop", "should_exit",
}


def _atomic_write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    # 轮换备份：保留上一个完好版本
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(state: dict, path: str = None) -> None:
    """过滤对象字段后原子保存运行状态。失败只打日志，不打断主流程。"""
    path = path or config.CHECKPOINT_FILE
    try:
        payload = {"version": CHECKPOINT_VERSION, "saved_at": time.time()}
        for k, v in state.items():
            if k in _EXCLUDED_FIELDS:
                continue
            try:
                json.dumps(v)
            except (TypeError, ValueError):
                continue
            payload[k] = v
        # exp_history 截尾
        hist = payload.get("exp_history")
        if isinstance(hist, list) and len(hist) > 500:
            payload["exp_history"] = hist[-500:]
        _atomic_write_json(path, payload)
    except Exception as e:
        print(f"[持久化] checkpoint 保存失败: {type(e).__name__}: {e}")


def load_checkpoint(path: str = None) -> dict | None:
    """加载 checkpoint，损坏时回退 .bak，都不可用返回 None。"""
    path = path or config.CHECKPOINT_FILE
    for candidate in (path, path + ".bak"):
        if not os.path.exists(candidate):
            continue
        try:
            data = _load_json(candidate)
            if data.get("version") != CHECKPOINT_VERSION:
                print(f"[持久化] checkpoint 版本不匹配（{candidate}），忽略。")
                continue
            data.pop("version", None)
            data.pop("saved_at", None)
            if candidate.endswith(".bak"):
                print("[持久化] 主 checkpoint 损坏，已从 .bak 恢复。")
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"[持久化] 读取 {candidate} 失败: {e}")
    return None


def load_credentials() -> dict | None:
    try:
        return _load_json(config.CREDENTIALS_FILE)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_credentials(creds: dict) -> None:
    _atomic_write_json(config.CREDENTIALS_FILE, creds)


_PROGRESS_HEADER = "ts,exp,exp_rate_1h,milestone,kee_pct,potential,deaths,quests_done,quests_skipped\n"


def append_progress(exp: int, rate_1h: float, milestone: str, kee_pct: int,
                    potential: int, deaths: int, quests_done: int, quests_skipped: int) -> None:
    """追加一行进度到 progress.csv（外部监控与停滞检测的数据源）。"""
    try:
        path = config.PROGRESS_CSV
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new_file:
                f.write(_PROGRESS_HEADER)
            f.write(f"{int(time.time())},{exp},{rate_1h:.1f},{milestone},"
                    f"{kee_pct},{potential},{deaths},{quests_done},{quests_skipped}\n")
    except Exception as e:
        print(f"[持久化] progress.csv 写入失败: {e}")


def calc_rate_1h(exp_history: list) -> float:
    """用 exp_history 最近1小时窗口算速率（exp/h）。"""
    if not exp_history or len(exp_history) < 2:
        return 0.0
    now = exp_history[-1][0]
    window = [p for p in exp_history if now - p[0] <= 3600]
    if len(window) < 2:
        window = exp_history[-2:]
    dt = window[-1][0] - window[0][0]
    if dt <= 0:
        return 0.0
    return (window[-1][1] - window[0][1]) * 3600.0 / dt


def log_death(detail: str) -> None:
    """死亡事件醒目告警日志。"""
    try:
        path = config.DEATHS_LOG
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [ALERT][DEATH] {detail}\n")
    except Exception as e:
        print(f"[持久化] deaths.log 写入失败: {e}")
