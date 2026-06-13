"""
最终验收例程：save → score 解析 → 存档文件双重验证 → 达标返回 goal_reached。
"""
import os
import re
import time

import config
from config import Colors
from mud.profile import cmd
from mud import profile
from mud.routines.base import Routine, RoutineResult, RoutineContext, \
    OUTCOME_COMPLETED, OUTCOME_FAILED, OUTCOME_GOAL


def read_savefile_exp(char_id: str) -> tuple[int | None, float | None]:
    """从 MUD 存档读取 combat_exp 与文件 mtime。"""
    if not char_id:
        return None, None
    path = os.path.join(config.MUD_PROJECT_DIR, "mudlib", "data", "user",
                        char_id[0], f"{char_id}.o")
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        m = re.search(r'"combat_exp":(\d+)', content)
        return (int(m.group(1)) if m else None), os.path.getmtime(path)
    except OSError:
        return None, None


class VerifyRoutine(Routine):
    name = "verify"

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        self.probe(ctx)
        if ctx.char.get("ghost"):
            return RoutineResult(OUTCOME_FAILED, "death: 角色死亡状态")

        # 1. score 解析
        sc = ctx.refresh_score()
        if not sc:
            return self.escalate(ctx, "验收失败", "无法解析 score 输出")
        score_exp = sc["exp"]

        # 2. save 落盘
        ctx.io.request(cmd.save(), deadline=6.0)
        time.sleep(1)

        # 3. 存档双重验证
        char_id = ctx.state.get("credentials", {}).get("id") or ctx.char.get("id", "")
        file_exp, mtime = read_savefile_exp(char_id)
        fresh = mtime is not None and (time.time() - mtime) < 300

        detail = (f"score经验={score_exp} 存档经验={file_exp} "
                  f"存档新鲜={'是' if fresh else '否'} 目标={config.GOAL_EXP}")
        ctx.log("验收", detail, Colors.GREEN)

        if score_exp >= config.GOAL_EXP and file_exp is not None \
                and file_exp >= config.GOAL_EXP and fresh:
            self._write_report(ctx, score_exp, file_exp)
            ctx.record_exp(score_exp, force_progress=True)
            ctx.checkpoint(force=True)
            return RoutineResult(OUTCOME_GOAL, f"双重验证通过！{detail}")

        if score_exp < config.GOAL_EXP:
            return RoutineResult(OUTCOME_FAILED, f"经验尚未达标: {detail}")
        return self.escalate(ctx, "验证不一致", detail)

    def _write_report(self, ctx, score_exp, file_exp):
        try:
            path = os.path.join(config.LOG_DIR, "final_report.md")
            c = ctx.counters
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    f"# 10万经验目标达成报告\n\n"
                    f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"- 角色: {ctx.char.get('id')}\n"
                    f"- score 实战经验: {score_exp}\n"
                    f"- 存档 combat_exp: {file_exp}\n"
                    f"- 完成任务数: {c.get('quests_done', 0)}（跳过 {c.get('quests_skipped', 0)}）\n"
                    f"- 死亡次数: {c.get('deaths', 0)}\n"
                    f"- 重连次数: {c.get('reconnects', 0)}\n"
                    f"- 经验曲线: logs/system/progress.csv\n")
        except OSError:
            pass
