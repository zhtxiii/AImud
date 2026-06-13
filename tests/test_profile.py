"""
mud/profile.py 解析器单元测试。
样本基于 mudlib 源码的精确输出格式（hp.c/score.c/look.c/combatd.c/god.c/logind.c/more.c）。
运行：python3 -m pytest tests/test_profile.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mud import profile
from mud.profile import cmd
from mud.protocol import TelnetFilter

HP_SAMPLE = """▼ 武林初学者张三(Newtest)
 精：  93/ 100 ( 93%)  灵力：   0/   0 (+0)
 气：  45/  90 ( 90%)  内力：  30/  60 (+0)
 神： 100/ 100 (100%)  食物：  80/ 200
 神： 100/ 100 (100%)  法力：   0/   0 (+0)
 食物：  80/  200      潜能： 57
 饮水：  20/  200      经验： 1234
"""

SCORE_SAMPLE = """▼ 武林初学者张三(Newtest)
 攻击力： 12 (+0)    防御力： 8 (+0)
 总共杀过 3 个人，其中有 0 个是其他玩家。
 杀    气： 30     潜    能： 57 (43%)
 实战经验： 1234    综合评价： 25
"""

LOOK_SAMPLE = """
▲ 武场
  这是武馆的练武场，宽敞结实的地面用石灰铺成。
  这里明显的出口是 north、east 和 west。
  武馆弟子 李四(Trainee)
  武馆弟子 王五(Trainee)
  拳法教练 李火狮(Li huoshi)
"""

LOOK_ONE_EXIT = """
▲ 饮风客栈
  这是雪亭镇唯一的一家客栈。
  这里唯一的出口是 east。
  店小二(Waiter)
  一枚金币(Coin)
"""

QUEST_SAMPLE = """朱鸿雪沉思了一会儿，说道：
请在三分二十秒内替我杀了『疯狗』。
"""

REWARD_SAMPLE = """恭喜你！你又完成了一项任务！
你被奖励了：
二十三点实战经验
十五点潜能
八点综合评价
"""

PAGER_SAMPLE = "== 未完继续 95% == (ENTER 继续下一页，q 离开，b 前一页)"


def test_cn2int():
    assert profile.cn2int("二十三") == 23
    assert profile.cn2int("一百零五") == 105
    assert profile.cn2int("三") == 3
    assert profile.cn2int("十") == 10
    assert profile.cn2int("十五") == 15
    assert profile.cn2int("两百") == 200
    assert profile.cn2int("一万二千三百四十五") == 12345
    assert profile.cn2int("42") == 42


def test_duration_cn():
    assert profile.duration_cn("三分二十秒") == 200
    assert profile.duration_cn("五十秒") == 50
    assert profile.duration_cn("一小时两分十秒") == 3730


def test_parse_hp():
    r = profile.parse_hp(HP_SAMPLE)
    assert r is not None
    assert r["kee"] == 45 and r["eff_kee"] == 90
    assert r["kee_pct"] == 50            # 45/90
    assert r["kee_integrity"] == 90      # eff/max → wounded
    assert r["wounded"] is True
    assert r["food"] == 80 and r["water"] == 20
    assert r["potential"] == 57
    assert r["exp"] == 1234
    assert r["force"] == 30


def test_parse_hp_none():
    assert profile.parse_hp("这里没有hp输出") is None


def test_parse_score():
    r = profile.parse_score(SCORE_SAMPLE)
    # ap/dp 还原下界：(显示值-1)*100
    assert r == {"exp": 1234, "potential": 57, "score": 25, "bellicosity": 30,
                 "ap": 1100, "dp": 700}


def test_parse_room():
    r = profile.parse_room(LOOK_SAMPLE)
    assert r["name"] == "武场"
    assert r["exits"] == ["north", "east", "west"]
    ids = [o["id"] for o in r["objects"]]
    assert ids.count("trainee") == 2
    assert "li huoshi" in ids
    cn = [o["cn"] for o in r["objects"]]
    assert "拳法教练 李火狮" in cn


def test_parse_room_one_exit():
    r = profile.parse_room(LOOK_ONE_EXIT)
    assert r["name"] == "饮风客栈"
    assert r["exits"] == ["east"]
    assert any(o["id"] == "waiter" for o in r["objects"])


def test_parse_room_combat_noise():
    noisy = "张三对你发起攻击！\n" + LOOK_SAMPLE + "\n李四攻击了你！"
    r = profile.parse_room(noisy)
    assert r["name"] == "武场"


def test_parse_quest_grant():
    r = profile.parse_quest_grant(QUEST_SAMPLE)
    assert r == {"type": "kill", "target_cn": "疯狗", "limit_sec": 200}
    assert profile.parse_quest_grant("朱鸿雪说道：就凭你这种小角色？") is None


def test_parse_reward():
    r = profile.parse_reward(REWARD_SAMPLE)
    assert r == {"exp": 23, "potential": 15}


def test_detect_events_login():
    evs = profile.detect_events("您的英文名字：")
    assert profile.has_event(evs, "LOGIN_NAME")
    evs = profile.detect_events("使用这个名字将会创造一个新的人物，您确定吗(y/n)？")
    assert profile.has_event(evs, "LOGIN_NEW_CONFIRM")
    evs = profile.detect_events("您要将另一个连线中的相同人物赶出去，取而代之吗？(y/n)")
    assert profile.has_event(evs, "LOGIN_TAKEOVER")
    evs = profile.detect_events("密码错误！")
    assert profile.has_event(evs, "PASSWORD_ERROR")


def test_detect_events_combat():
    evs = profile.detect_events("你的「拳脚」进步了！")
    e = profile.has_event(evs, "SKILL_IMPROVED")
    assert e and e["groups"][0] == "拳脚"
    evs = profile.detect_events("武馆弟子脚下一个不稳，跌在地上一动也不动了。")
    assert profile.has_event(evs, "OPPONENT_DOWN")
    evs = profile.detect_events("看起来武馆弟子并不想跟你较量。")
    e = profile.has_event(evs, "FIGHT_REFUSED")
    assert e and "武馆弟子" in e["groups"][0]
    evs = profile.detect_events("你慌里慌张往北边(north)逃去...")
    assert profile.has_event(evs, "SELF_FLEE")
    evs = profile.detect_events("你说道：「不打了，不打了，我投降....。」")
    assert profile.has_event(evs, "SELF_SURRENDER")


def test_detect_events_death_and_quest():
    evs = profile.detect_events("你死了。")
    assert profile.has_event(evs, "SELF_DEATH")
    evs = profile.detect_events("疯狗死了。")
    assert profile.has_event(evs, "SOMEONE_DIED")
    assert not profile.has_event(evs, "SELF_DEATH")
    evs = profile.detect_events(REWARD_SAMPLE)
    assert profile.has_event(evs, "QUEST_DONE")
    assert profile.has_event(evs, "REWARD")
    evs = profile.detect_events(PAGER_SAMPLE)
    assert profile.has_event(evs, "PAGER")


def test_cmd_builders():
    assert cmd.fight("trainee") == "fight trainee"
    assert cmd.fight("trainee", 2) == "fight trainee 2"
    assert cmd.kill("dog") == "kill dog"
    assert cmd.go("southup") == "southup"
    assert cmd.learn("dodge", "liu chunfeng") == "learn dodge from liu chunfeng"
    assert cmd.set_wimpy(20) == "set wimpy 20"
    assert cmd.ask("self", "回家") == "ask self about 回家"


def test_cmd_injection_blocked():
    import pytest
    with pytest.raises(ValueError):
        cmd.kill("dog; quit")
    with pytest.raises(ValueError):
        cmd.go("north\nquit")
    with pytest.raises(ValueError):
        cmd.ask("self", "a\nquit")


def test_telnet_filter():
    f = TelnetFilter()
    # IAC WILL ECHO 夹在数据中
    out = f.feed(b"hel" + bytes([255, 251, 1]) + b"lo")
    assert out == b"hello"
    # IAC 序列跨块
    out1 = f.feed(b"ab" + bytes([255]))
    out2 = f.feed(bytes([253, 24]) + b"cd")
    assert out1 + out2 == b"abcd"
    # 子协商跨块
    f2 = TelnetFilter()
    o1 = f2.feed(b"x" + bytes([255, 250, 24, 1]))
    o2 = f2.feed(bytes([255, 240]) + b"y")
    assert o1 + o2 == b"xy"


def test_utf8_split_across_chunks():
    """跨块半个汉字不能丢。"""
    import codecs
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    data = "气：100/100".encode("utf-8")
    part1, part2 = data[:2], data[2:]  # "气" 的 3 字节被切开
    text = dec.decode(part1) + dec.decode(part2)
    assert text == "气：100/100"
