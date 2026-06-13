"""
grind planner 流转逻辑测试（不连 MUD、不调 LLM）。
运行：python3 -m pytest tests/test_planner_flow.py -q
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["AGENT_MODE"] = "grind"

from nodes.planner import _grind_planner, _build_repair_task
from mud import milestones


def base_state(**kw):
    s = {
        "char_status": {}, "milestone": {}, "counters": {},
        "exp_history": [], "current_task": {}, "escalation": {},
        "task_completed": False, "task_stuck": False,
        "knowledge_base": [], "phase": 1,
    }
    s.update(kw)
    return s


def test_m0_login_first():
    s = base_state()
    out = _grind_planner(s)
    assert out["current_task"]["executor"] == "routine:login"


def test_m0_bootstrap_after_login():
    s = base_state(char_status={"logged_in": True})
    out = _grind_planner(s)
    assert out["current_task"]["executor"] == "routine:bootstrap"


def test_m1_spar():
    s = base_state(char_status={"logged_in": True, "bootstrap_done": True, "exp": 100})
    out = _grind_planner(s)
    assert out["current_task"]["executor"] == "routine:spar"
    assert out["current_task"]["params"]["target_exp"] == 1100


def test_m2_quest_spar_alternation():
    s = base_state(char_status={"logged_in": True, "bootstrap_done": True, "exp": 5000})
    seen = []
    for _ in range(4):
        out = _grind_planner(s)
        seen.append(out["current_task"]["executor"])
        s["current_task"] = out["current_task"]
        s["milestone"] = out["milestone"]
    assert "routine:quest" in seen and "routine:spar" in seen


def test_m2_maintain_cycle():
    s = base_state(char_status={"logged_in": True, "bootstrap_done": True, "exp": 5000})
    execs = []
    for _ in range(14):
        out = _grind_planner(s)
        execs.append(out["current_task"]["executor"])
        s["current_task"] = out["current_task"]
        s["milestone"] = out["milestone"]
    assert "routine:maintain" in execs


def test_death_priority():
    s = base_state(char_status={"logged_in": True, "bootstrap_done": True,
                                "exp": 5000, "ghost": True})
    out = _grind_planner(s)
    assert out["current_task"]["executor"] == "routine:death_recovery"


def test_m3_verify():
    s = base_state(char_status={"logged_in": True, "bootstrap_done": True, "exp": 100001})
    out = _grind_planner(s)
    assert out["current_task"]["executor"] == "routine:verify"


def test_goal_reached_after_verify():
    s = base_state(
        char_status={"logged_in": True, "bootstrap_done": True, "exp": 100001},
        current_task={"id": "G-9", "executor": "routine:verify", "status": "completed",
                      "result": "双重验证通过"})
    out = _grind_planner(s)
    assert out["exit_reason"] == "goal_reached"


def test_escalation_dispatches_repair():
    s = base_state(
        char_status={"logged_in": True, "bootstrap_done": True, "exp": 3000},
        escalation={"routine": "spar", "reason": "迷路", "detail": "x",
                    "room": "d/snow/inn", "room_label": "饮风客栈",
                    "recent_output": "", "char_status": {}})
    out = _grind_planner(s)
    assert out["current_task"]["executor"] == "llm"
    assert out["current_task"]["id"].startswith("R-")
    assert out["escalation"]["repair_dispatched"] is True


def test_repeated_escalation_fatal():
    s = base_state(char_status={"logged_in": True, "bootstrap_done": True, "exp": 3000})
    s["counters"]["esc_spar:迷路"] = 3
    s["escalation"] = {"routine": "spar", "reason": "迷路", "detail": "x",
                       "room": None, "room_label": "", "recent_output": "",
                       "char_status": {}}
    out = _grind_planner(s)
    assert out["exit_reason"] == "fatal"


def test_repair_completion_resumes_milestone():
    s = base_state(
        char_status={"logged_in": True, "bootstrap_done": True, "exp": 3000},
        current_task={"id": "R-123", "executor": "llm", "status": "completed",
                      "result": "已回到安全位置"},
        task_completed=True,
        escalation={"routine": "spar", "reason": "迷路", "repair_dispatched": True},
        llm=None)
    out = _grind_planner(s)
    # 修复完成 → escalation 清空 → 回到里程碑任务
    assert out["escalation"] == {}
    assert out["current_task"]["executor"].startswith("routine:")


def test_hard_stall_fatal():
    s = base_state(
        char_status={"logged_in": True, "bootstrap_done": True, "exp": 3000},
        exp_history=[[time.time() - 7200, 3000]])
    out = _grind_planner(s)
    assert out["exit_reason"] == "fatal"


def test_no_stall_when_fresh():
    s = base_state(
        char_status={"logged_in": True, "bootstrap_done": True, "exp": 3000},
        exp_history=[[time.time() - 60, 3000]])
    out = _grind_planner(s)
    assert out.get("exit_reason") != "fatal"
