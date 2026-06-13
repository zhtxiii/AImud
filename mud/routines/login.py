"""
登录例程：处理建号/老号登录/崩溃重连接管的完整状态机。
原则：绝不猜密码（密码错≠重试别的密码，而是升级）。
"""
import random
import string
import time

import persistence
from config import Colors
from mud import profile
from mud.profile import cmd
from mud.routines.base import Routine, RoutineResult, RoutineContext, \
    OUTCOME_COMPLETED, OUTCOME_FAILED


def _gen_credentials() -> dict:
    cid = "ai" + "".join(random.choices(string.ascii_lowercase, k=6))
    return {
        "id": cid,
        "password": "".join(random.choices(string.ascii_letters + string.digits, k=12)),
        # logind 的 strlen 按字节校验（≤6），UTF-8 下中文名最多 2 个汉字
        "cn_name": "小练",
        "email": "bot@test.local",
        "gender": "m",
        "created": False,
    }


class LoginRoutine(Routine):
    name = "login"
    MAX_ROUNDS = 30

    def execute(self, ctx: RoutineContext, params: dict) -> RoutineResult:
        creds = ctx.state.get("credentials") or {}
        if not creds.get("id"):
            creds = _gen_credentials()
            ctx.state["credentials"] = creds
            persistence.save_credentials(creds)
            ctx.log("登录", f"已生成新角色凭据: {creds['id']}", Colors.YELLOW)

        password_sent = 0
        last_etype = None
        same_etype_count = 0
        text = ctx.io.drain(quiet=0.5, deadline=6.0)  # 接收欢迎横幅+名字提示

        for _round in range(self.MAX_ROUNDS):
            if ctx.stop_requested():
                from mud.routines.base import OUTCOME_STOPPED
                return RoutineResult(OUTCOME_STOPPED, "停止信号")

            events = profile.detect_events(text)

            # --- 登录成功判定：能解析出房间或 score ---
            if profile.parse_room(text) or (profile.parse_score(text) and "实战经验" in text):
                return self._post_login(ctx, creds)

            # --- 已在游戏内判定：断线重连会被服务器直接附身进游戏 ---
            # （此时登录提示不会出现，输入会被当作游戏命令 → "什么？"）
            if profile.has_event(events, "CONFUSED_CMD") or "> " in text[-10:]:
                hp_text = ctx.io.request(cmd.hp(), quiet=0.5, deadline=5.0)
                if profile.parse_hp(hp_text):
                    ctx.log("登录", "检测到已在游戏内（断线重连自动附身）", Colors.GREEN)
                    return self._post_login(ctx, creds)

            if profile.has_event(events, "PASSWORD_ERROR"):
                if creds.get("created"):
                    # 我们存的密码被拒 → 绝不猜，直接升级
                    return self.escalate(ctx, "密码被拒",
                                         f"角色 {creds['id']} 的已存密码被拒绝，需要人工/LLM 介入")
                # 从未成功建过号却提示密码错 → 撞了别人的名字，换名重来
                ctx.log("登录", "名字已被占用且密码不符，更换角色名", Colors.YELLOW)
                creds = _gen_credentials()
                ctx.state["credentials"] = creds
                persistence.save_credentials(creds)
                text = ctx.io.drain(quiet=0.5, deadline=5.0)
                continue

            ev = profile.has_event(
                events, "LOGIN_NAME", "LOGIN_NEW_CONFIRM", "LOGIN_PASSWORD",
                "LOGIN_SET_PASSWORD", "LOGIN_CONFIRM_PASSWORD", "LOGIN_CN_NAME",
                "LOGIN_EMAIL", "LOGIN_GENDER", "LOGIN_TAKEOVER")

            if ev is None:
                # 没有已知提示 → 等一拍再收
                more = ctx.io.drain(quiet=0.5, deadline=4.0)
                if more:
                    text = more
                    continue
                # 静默：先探测是否已在游戏内，再考虑发送名字
                hp_text = ctx.io.request(cmd.hp(), quiet=0.5, deadline=5.0)
                if profile.parse_hp(hp_text):
                    ctx.log("登录", "hp 探测确认已在游戏内", Colors.GREEN)
                    return self._post_login(ctx, creds)
                text = ctx.io.request(creds["id"], quiet=0.5, deadline=6.0)
                continue

            etype = ev["type"]
            if etype == last_etype:
                same_etype_count += 1
                if same_etype_count >= 4:
                    return self.escalate(ctx, "登录提示循环",
                                         f"同一提示 {etype} 反复出现，最近输出: {text[:300]}")
            else:
                last_etype = etype
                same_etype_count = 0

            if etype == "LOGIN_NAME":
                text = ctx.io.request(creds["id"], quiet=0.5, deadline=6.0)
            elif etype == "LOGIN_NEW_CONFIRM":
                if creds.get("created"):
                    ctx.log("登录", "警告：老号被识别为新建（存档可能被清），按新建流程走", Colors.YELLOW)
                text = ctx.io.request("y", quiet=0.5, deadline=6.0)
            elif etype == "LOGIN_PASSWORD":
                password_sent += 1
                if password_sent > 3:
                    return self.escalate(ctx, "登录循环", "密码提示反复出现")
                text = ctx.io.request(creds["password"], quiet=0.6, deadline=8.0)
            elif etype == "LOGIN_SET_PASSWORD":
                text = ctx.io.request(creds["password"], quiet=0.5, deadline=6.0)
            elif etype == "LOGIN_CONFIRM_PASSWORD":
                text = ctx.io.request(creds["password"], quiet=0.5, deadline=6.0)
            elif etype == "LOGIN_CN_NAME":
                text = ctx.io.request(creds["cn_name"], quiet=0.5, deadline=6.0)
            elif etype == "LOGIN_EMAIL":
                text = ctx.io.request(creds["email"], quiet=0.5, deadline=6.0)
            elif etype == "LOGIN_GENDER":
                text = ctx.io.request(creds["gender"], quiet=0.8, deadline=8.0)
            elif etype == "LOGIN_TAKEOVER":
                ctx.log("登录", "检测到残留连线，接管", Colors.YELLOW)
                text = ctx.io.request("y", quiet=0.8, deadline=8.0)

        return self.escalate(ctx, "登录超轮数", f"{self.MAX_ROUNDS} 轮未完成登录")

    # ------------------------------------------------------------------
    def _post_login(self, ctx: RoutineContext, creds: dict) -> RoutineResult:
        """登录成功后：标记凭据、wimpy、探测状态。"""
        if not creds.get("created"):
            creds["created"] = True
            ctx.state["credentials"] = creds
            persistence.save_credentials(creds)
        ctx.char["logged_in"] = True
        ctx.char["id"] = creds["id"]

        import config as _cfg
        ctx.io.request(cmd.set_wimpy(_cfg.WIMPY_PCT), deadline=4.0)
        hp = self.probe(ctx)
        sc = ctx.refresh_score()
        if sc:
            ctx.record_exp(sc["exp"], force_progress=True)

        # 死号检测/纠正
        node = ctx.char.get("location_node", "") or ""
        if node.startswith("d/death") or (hp and hp.get("eff_kee", 99) <= 1 and hp.get("exp", 1) > 0):
            ctx.char["ghost"] = True
            ctx.log("登录", "登录后发现角色处于死亡状态，待复活", Colors.RED)
        elif ctx.char.get("ghost") and hp and hp.get("eff_kee", 0) > 5:
            ctx.char["ghost"] = False
            ctx.log("登录", "ghost 标志过期（已复活），清除", Colors.GREEN)

        ctx.checkpoint(force=True)
        exp = ctx.char.get("exp", 0)
        ctx.log("登录", f"登录成功: {creds['id']} 经验={exp} 位置={node}", Colors.GREEN)
        return RoutineResult(OUTCOME_COMPLETED,
                             f"登录成功 id={creds['id']} exp={exp} 位置={node}")
