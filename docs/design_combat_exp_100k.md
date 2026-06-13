# 实现设计：无人值守刷满 100,000 实战经验

目标：智能体无人值守连续运行 1~3 天，在原版 ES2 MUD（127.0.0.1:4000）上把一个角色练到
combat_exp ≥ 100,000，以 `score` 输出与存档文件双重验证。
架构决策（已确定）：混合架构 —— 确定性例程跑主循环，LLM（DeepSeek）只做规划、异常处理与僵局决策。

本文档基于对 agent 全部源码（agent.py / graph.py / state.py / nodes/* / llm_client.py /
connection_manager.py / config.py / 运行观察报告）和 MUD 源码（combatd.c / god.c / qlist*.c /
fight.c / learn.c / damage.c / logind.c / 各 NPC 与房间文件）的逐行核验写成。

---

## 1. 调研核验结果：确认、修正与新发现

### 1.1 已确认的关键机制（与你的理解一致）

| 机制 | 核验出处 |
|---|---|
| 杀怪本身不给经验；经验来自战斗事件概率 +1 和任务结算 | combatd.c do_attack / killer_reward |
| 攻方 +1exp+1pot 条件：`ap < dp` 且 `random(gin%+int) > 30`，且交战一方为 NPC | combatd.c (7) Give experience |
| 防方闪避/招架 +1exp+技能成长 条件：`dp < ap` 且 `random(gin%+int) > 50` | combatd.c (3)(4) |
| 被重击 +1exp+1pot 条件：`random(max_kee+kee) < damage` | combatd.c (7) |
| 任务结算 `exp_bonus/2 + random(exp_bonus/2)`，限时 40~500s，超时再领罚 kee 减半、tfinished 清零 | god.c + combatd.c killer_reward |
| 领任务门槛 combat_exp > 1000（≤1000 被骂走） | god.c L89 |
| 每完成 3 单升 1 档（num += tfinished/3），tfinished>9 归零回落 | god.c L139-148, combatd.c |
| fight 对 can_speak NPC 是切磋；对动物自动变真打（对方 kill_ob） | cmds/std/fight.c |
| 切磋中对方持武器仍造成真伤（wound 条件 `is_killing \|\| weapon`） | combatd.c (6) |
| NPC accept_fight 需 gin/kee/sen 三项均 ≥90%，且未在战斗中 | std/char/npc.c |
| 死亡 -10% exp、全技能惩罚、变鬼去 /d/death/gate | combatd.c killer_reward |
| `set wimpy 30` 写 env/wimpy 生效；usr/wimpy.c 写错字段（`wimpy`）确实无效 | cmds/usr/set.c + std/char.c L87 |
| 自然恢复：water<1 时玩家 gin/kee/sen **完全不回**；food<1 时不回内力 | feature/damage.c heal_up |
| exert recover：耗 20 内力回 `10+force技能/3` 气 | d/force/recover.c |
| learn 封顶：martial 技能 `level³/10 ≤ combat_exp`，且 learned_points < potential，耗精 | cmds/std/learn.c |
| 存档 `mudlib/data/user/<首字母>/<id>.o`，单行 dbase 含 `"combat_exp":N`，可 grep | data/user/t/test.o 实查 |
| 心跳 1000ms（战斗节奏 1 秒/轮） | bin/config.ES2 |
| 分页提示精确格式 `== 未完继续 N% == (ENTER 继续下一页，q 离开，b 前一页)` | feature/more.c |
| score 含 `实战经验： N`；hp 含 气/内力/食物/饮水/潜能/经验，全部可正则解析 | cmds/usr/score.c, hp.c |
| 复活：/d/death/inn1 内 `ask <自己的id> about 回家` → 复活并传送到 /d/snow/temple | d/death/inn1.c |
| 登录提示串全部确认（英文名/密码/y确认/中文名/密码×2/性别等） | adm/daemons/logind.c |

### 1.2 必须修正你的设计的发现（按严重度排序）

**F1（高危）mud_map.json 完全不含 u/cloud 区域。**
map_generator.py 的 `SEARCH_DIR` 写死为 `mudlib/d`，523 个节点全部在 /d/*。
朱鸿雪（u/cloud/god2）、绮云镇（u/cloud/entrance）、卧龙岗（u/cloud/dragonhill/*）都不在图里；
另有 22 个节点 path 为空（解析失败的脏数据）。qlist1000 的多数目标（茶工/裁缝/家丁/李师师/宝官…）
住在 u/cloud。**QuestRoutine 的导航根本无图可用，必须重新生成地图覆盖 d/ + u/，并清洗空节点。**
好消息：map_generator.py 是纯静态分析，改 SEARCH_DIR 重跑即可，已验证雪镇关键房间均在图中。

**F2（高危）任务经验不随档位增长，任务不能当主经验来源。**
实测各档 exp_bonus 均值：qlist1000≈32、qlist5000≈32、qlist10000≈27、qlist40000≈29、qlist100000≈49。
平均每单实得 ≈ 0.75×exp_bonus ≈ **20~37 exp**。纯靠任务到 10 万需 3000+ 单 ×（往返+击杀+等待）≈ 100~250 小时，不可行。
**主力必须是切磋；任务的价值是 potential 供给（learn 的硬通货）、节奏调剂和额外 exp。** M2 的设计重心要据此调整。

**F3（高）李火狮只和封山剑派弟子切磋、只教本派弟子。**
fist_trainer.c 的 `accept_fight`/`recognize_apprentice` 都检查 `family == "封山剑派"`。
所以 **拜师柳淳风是 M0 的硬前置**（attempt_apprentice 条件 cor≥20 且 cps≥20，新角色八维全 30，满足）。
拜师后李火狮（exp 3000，徒手，无武器，就在镇内 school2）是中期最重要的安全陪练。

**F4（高）切磋经验速率的甜点区会快速漂移，"梯子断档"是真实风险。**
`skill_power = level³/3 ÷ max_sen × sen + combat_exp`（无技能时 = exp/2）。我方把技能学到经验封顶
（level=(10·exp)^⅓）时 ap≈4.3×exp，即**我方战力随 exp 近似线性膨胀**：
- trainee（dp=50）：仅在我方 exp<~100 时有攻击通道价值，学了技能后立即"打不出经验"；
- 中间档：旅客 traveller（exp 600-1000，dodge50 → dp≈40k 上下）命中率掉到 ~10%，且广场的 trav_blade **持飞刀，切磋会受真伤**；
- 李火狮（dp≈12k）适合 exp ~1k→8k 区间；
- 10k→100k 的后期陪练目前没有逐个确认（候选：守卫刘安禄 exp 20000、云镇 bfighter、镖局武师等），
  **必须用 NPC 索引自动计算全服 NPC 的 ap/dp 估值来构建阶梯**（见 §4.4），并接受"攻击技能不必学满"这个调节旋钮
  （ap 含 +exp 项，技能压低只能有限延长目标寿命）。
另外低 exp 期"被重击通道"（挨打 +1）是冷启动主力：新角色被 trainee 揍，期望 ≈0.1 exp/秒级别。

**F5（高）速率预算：1~3 天达标"可行但紧张"，必须设标定门。**
理论模型（心跳 1s，双方各攻 1 次/轮）：甜点匹配下三通道合计 ≈ 0.4~0.7 exp/s，計入恢复/轮换/吃喝占空比
60~80% → **1200~2500 exp/h**；任务混入再 +400~800/h 等效。100k ≈ 35~70 小时纯运行 ≈ 1.5~3 天连续无故障。
结论：目标在预算边缘。M1 必须实测标定（见 §6.2），若 <1500/h 要立即启用备选（多 NPC 同时切磋、
带药切磋武装 NPC、调整任务配比），并向你报告修正 ETA，而不是闷头跑。

**F6（中）surrender 是切磋的正确退出方式，而不是 go 逃跑。**
cmds/std/surrender.c：切磋中投降 → `remove_all_enemy()` 干净脱战，代价 -50 score（无关紧要）；
对 is_killing 的敌人无效（真打只能 go 逃跑）。而 wimpy 自动逃跑（GO_CMD->do_flee）**方向随机**，
逃完位置不可控。所以 SparRoutine 主动用 surrender 在 40% 气脱战，wimpy 20% 只作最后保险，逃跑后必须重定位。

**F7（中）任务不能主动放弃，必须等超时。**
god.c：有未过期任务时 `quest` 直接 return 0（落到 usr/quest.c 只显示状态）。跳过一个不可达目标的成本
= 剩余倒计时（40~500s）。任务循环效率 = 白名单覆盖率的函数；等待期不空转（原地切磋/恢复/learn）。
另外超时罚（kee/2+1 与 tfinished 清零）很轻，**故意超时还可以把任务档位拉回低档**，是合法的策略旋钮。

**F8（中）领任务必须每单都回朱鸿雪房间，唯一通道经过卧龙岗。**
雪镇 sroad1 → dragonhill/nroad → nhillfoot → **hummock（2×aggressive 持刀 Gangster，exp 10000）** → shillfoot → sroad → 绮云镇，无旁路。
任务期主基地应设在云镇侧（多数 qlist 目标也在云镇），尽量减少穿山次数；穿山用"连发两步不停留"协议（§5.3）。

**F9（中）新角色 food=0、water=0 出生**（test.o 实查）——开局**完全没有自然恢复**。
M0 第一批动作必须包含：inn 地上捡钱（房间常驻 `/obj/money/coin:99`，每 reset 刷新）→ 找店小二
buy dumpling/wineskin → eat/drink。这是冷启动的生死步骤，不是 nice-to-have。

**F10（中）现行代码一个未发现的隐患：LangGraph recursion_limit 未配置。**
langgraph 1.2.2 默认 recursion_limit=25，每轮执行循环消耗 4~5 个 super-step，即每个 invoke 理论上
几轮后就会抛 GraphRecursionError，被 agent.py 的裸 `except Exception` 吞掉后整图重启（伪装成"偶尔重启"）。
例程化后步数大减，但仍必须显式设置 `invoke(state, config={"recursion_limit": 1000})` 并单独捕获该异常。

**F11（低）NPC 老师的"精神"不会被扣**（learn.c 只对 userp 老师扣 sen）→ 跟 NPC 学习仅受自己的
精(gin)与 potential 限制，可高频 learn。
**F12（低）`save` 命令无冷却**，且在 valid_startroom（客栈/武馆大厅）执行会顺带把重生点设在该房间
—— MaintainRoutine 定期 save，一举三得（持久化+设重生点+给外部验证产生新鲜存档）。
**F13（低）杀气(bellicosity) 无守卫报复机制**，长期刷杀人任务不会引来系统性惩罚。
**F14（低）切磋徒手 NPC 理论上死不了**（无武器不产生 wound，气≤0 只是晕厥脱战）→ 无人值守的
安全核心是"只与徒手 NPC 长时间切磋"；死亡风险集中在：真打任务、aggressive NPC、武装陪练、穿越卧龙岗。

---

## 2. 对初步设计的批判性审查

总评：方向正确（例程化 + 里程碑 Planner + 工程加固 + 外部验证），与代码现状兼容性好。
以下按你点名的问题逐一回答，再列遗漏。

### 2.1 你点名的七个问题

**Q1 切磋速率是否足够？** 见 F2/F4/F5：勉强够但无余量。设计上的回应是三件事：
① M1 结束设"标定门"（实测 exp/h 写进 progress.csv，低于 1500/h 触发策略调整并告警）；
② NPC 索引自动算全服 ap/dp 构建陪练阶梯，避免梯子断档时人肉找目标；
③ 把"多 NPC 同时切磋"（fight A; fight B，防守通道事件×N）和"带金疮药切磋武装 NPC"列为已设计好的
备选加速器，标定不达标时按开关启用。

**Q2 trainee 健康<90% 不应战怎么轮换？** school2 同房 6 个 trainee。方案分三层：
① `fight trainee 2/3/...` 编号寻址（ES2 present() 对 "id n" 的支持需在 M1 现场验证，列为首批验证项）；
② 若编号不可用：SparRoutine 维护"本房间拒战计数"，拒战后 `look` 重选，全部拒战则进入 ROTATE_WAIT
（NPC 恢复很快：heal_up 不受食水限制，每 5~15s 回 con/3，2~4 分钟回满）；
③ 双房轮换：school2 ↔ square/inn 两个驻点交替。实际上 trainee 只服务开局 1~2 小时（F4），之后换李火狮，
他一个人 + 90% 门槛决定了"打一阵歇一阵"本来就是节奏的一部分，歇时正好 learn/吃喝。

**Q3 exp 1000 前怎么过渡？** 修正后的 M1：
0→~100：被打为主（trainee 群殴通道）+ 技能 cap 极低，learn 到 cap 即可；
~100→1000：技能 cap 升到 10~21，learn dodge/parry/unarmed/force，攻击通道恢复，trainee 失效后
转李火狮（需先拜师，F3）；1000+ 解锁朱鸿雪。全程不碰武装/动物/aggressive NPC。

**Q4 任务目标 NPC名→位置 怎么解析？** 新增静态生成的 **npc_index.json**（§4.4）：
扫描全 mudlib 房间文件的 `set("objects", …)` + NPC 文件的 `set_name("中文名", ({ids}))`，
产出 中文名 → [{file, ids, rooms[], exp, skills, dp_est, ap_est, armed, can_speak, attitude}]。
kill 用英文 id 寻址（`kill beggar`），结算匹配的是中文 name(1)（combatd 已核验），二者都从索引来。
未入白名单/不可达/评估打不过 → 走 F7 的超时跳过。索引在 MUD 外离线生成，运行时只读。

**Q5 例程与 LangGraph 怎么干净集成（节点长时间不返回的影响）？**
节点就是普通 Python 函数，长阻塞本身无害；真正的影响是四个，逐一处理：
① 图层面的状态快照在节点边界才发生 → 例程**内部**自行 checkpoint（每个任务周期/每 N 轮切磋/每 60s）；
② recursion_limit 与本问题无关但必须显式设置（F10）；
③ 图不能在例程中途响应断线 → 例程把 socket 断开作为一种 outcome（`reconnect`）返回，路由到 END，
外层重连后 planner 看到 in_progress 的例程任务原样重派，例程启动时一律先"探测现状"（hp/look 重定位）
保证可重入；
④ SIGTERM 缺省直接杀进程（finally 都不走）→ 注册信号处理，置停止标志，例程每轮检查，
优雅 checkpoint 后退出（stop_agent.sh 配合改成 TERM→等待→KILL）。
路由改动极小：planner 产出的任务带 `executor` 字段，`routine:*` 进新增的 routine_exec 节点，
`llm` 走原 observe/analyze/act 环（详见 §4.6）。

**Q6 checkpoint 恢复时 socket/llm 对象怎么处理？** 不持久化对象。checkpoint.json 只存纯数据
（milestone、tasks、char_status、exp_history、计数器）；client/llm 永远在 main() 重建后注入 state。
凭据单独存 credentials.json。例程恢复不靠"恢复执行点"，靠"探测现状 + 状态机从 INIT 重新收敛"
（所有例程状态机第一个状态都是 PROBE，幂等）。

**Q7 MUD 重启/被困/恢复期做什么？**
- MUD 重启/拒连：外层 ConnectSupervisor 指数退避（5s→60s 封顶）+ 醒目告警日志；连续 10 分钟拒连且
  配置 `AUTO_START_MUD=1` 时调用 bin/startmud（本地自有服务器，授权明确）。重连后 LoginRoutine 处理
  "把另一个连线赶出去 (y/n)" 接管提示（logind.c 已确认该提示存在，崩溃重连必遇）。
- 被困/迷路：NavigateRoutine 的 RELOCALIZE 协议（look→房名匹配图节点→失败则试反向退路→3 次失败
  escalate 给 LLM，LLM 修复预算 15 轮）。黑名单房间（d/wiz 全域、hummock 停留、死亡区以外的 no_fight 房）写死在 world.py。
- 恢复等待期（气<阈值、任务超时等待、NPC 不应战）：按优先级做 学习(learn 耗精不耗气)→吃喝补给→
  apply medicine→save→hp 轮询，绝不空转。

### 2.2 你的方案遗漏的点

1. **接管提示**：崩溃后重连必然遇到"您要将另一个连线中的相同人物赶出去吗？(y/n)"，LoginRoutine 必须处理（你的登录状态机没列）。
2. **UTF-8 截断**：现 receive() 对每个 4096 块独立 decode(errors=ignore)，中文多字节字符跨块会被**吃掉半个字**，
   战斗刷屏时正则会漏匹配。drain 读取器必须字节级缓冲 + 增量解码（§4.2）。
3. **KB 线程池堆积**是观测到的 2 分钟挂起的根因之一：单 worker + future 120s 超时放弃但线程还占着池子，
   后续任务排队堆积。修复不只是"加日志"：例程任务期整体禁用 KB/reflector + LLM 任务期 skip-if-busy（§4.7）。
4. **经验/反思库的污染控制**你提了去重封顶，但更重要的是：**例程任务不进反思**（它们没有可学的东西，
   还会刷屏污染），只有 LLM 任务和升级修复任务才反思。
5. **金钱与物资经济**：买食物/药需要钱。来源：inn 地板 99 coin（每 reset 刷新）+ 任务击杀目标尸体拾取
   （QuestRoutine 加 LOOT 步骤）。需要 money 字段进 char_status 与补给预算检查。
6. **任务时限解析**是中文数字（"三分二十秒"）：写 chinese_number 反解析器，或直接用本地时钟
   （接单时刻 + qlist 静态表里该目标的 time 字段）双保险。
7. **最终"原版"判定**：验收时要证明 mudlib 未被改动（git status/diff 干净 + config.ES2 heartbeat=1000），
   开发期加速只允许用于例程回归，标定与正式跑必须原版（写进验收清单，防止"作弊嫌疑"）。
8. **退出语义**：现在 should_stop/should_exit/should_reconnect 三个布尔并存，例程化后再加 outcome 会更乱。
   统一为 `exit_reason` 枚举（none/reconnect/stop/fatal/goal_reached），一次性理清（process_flow_issues.md #10）。
9. **死亡后的策略反应**：死一次 -10% exp（10 万时就是 -1 万）。除了 DeathRecovery 复活流程，还需要
   "事后保守化"：死亡计数进 checkpoint，planner 在死亡后把当前阶梯目标降一档、24h 内禁用该目标。
10. **进度死人开关**：watchdog 只看进程活着不够 —— 例程可能活着但卡死（如分页死循环）。
    以 progress.csv 的 mtime + exp 增量做"30 分钟无进展"判定，触发进程内自愈（例程 escalate）或外部重启。

---

## 3. 总体架构

```
┌────────────────────────────── run_agent.sh (watchdog: 重启限频/日志轮转) ─┐
│ agent.py main()                                                          │
│  ├ 加载 checkpoint.json → current_state(纯数据) + 重建 client/llm 注入     │
│  ├ ConnectSupervisor: connect 退避 / AUTO_START_MUD                       │
│  └ while: compiled_graph.invoke(state, recursion_limit=1000)              │
│      planner(里程碑驱动,代码为主)                                          │
│        ├ executor=="routine:*" → routine_exec ──┐                         │
│        └ executor=="llm"      → observe→analyze→act 环 (原逻辑,保留KB)     │
│      routine_exec: RoutineContext(socket直收发/解析器/世界图/检查点)        │
│        outcome: completed / failed / escalate / reconnect / stopped       │
│        escalate → planner 生成"修复任务"(executor=llm,预算15轮) → 修完重派   │
└───────────────────────────────────────────────────────────────────────────┘
离线资产(tools/ 一次性生成):  world_map.json (d/+u/ 全图)   npc_index.json
运行时产物: checkpoint.json  credentials.json  logs/progress.csv  logs/deaths.log
外部验证:  status.sh  → grep 存档 combat_exp + progress.csv ETA
```

里程碑骨架（代码写死，LLM 不参与生成）：

| 里程碑 | 内容 | 完成判据 |
|---|---|---|
| M0 立足 | 登录/建号→捡钱→买食水→eat/drink→set wimpy 20→拜师柳淳风→learn 到 cap→save | char_status 完整且 food/water>0、family=封山剑派 |
| M1 雪镇切磋 | trainee 群战起步→李火狮主力；潜能→learn 循环；**速率标定** | exp≥2000 且产出标定报告(exp/h 曲线) |
| M2 云镇双循环 | 穿山驻云镇；QuestRoutine(白名单) ⨉ SparRoutine(阶梯目标) 交替；MaintainRoutine 维持 | exp≥100,000 |
| M3 验收 | save→score 解析→存档 grep→生成报告 | 双验证均 ≥100,000 |

每个里程碑由若干预定义任务实例化（带 executor 与参数），planner 的 LLM 职责收缩为：
僵局/升级修复、阶梯目标微调建议、M 边界上的 sanity 评估。

---

## 4. 分模块实现计划

实现顺序即编号顺序；标注 [依赖]。

### 4.1 P0 工程地基（先于一切例程）

**llm_client.py（改）**
- `call_with_retry(..., max_retries=5, fail_mode="raise")`：超限抛 `LLMFailure`；
  保留无限重试仅当显式 `fail_mode="forever"`（不再是默认）。
- 调用超时从 600s 降到 120s（v4-flash 足够），减少线程挂死窗口。

**config.py（改）**
- `select_model()` 支持 `AGENT_MODEL=1/2` 环境变量直选（无 stdin 不阻塞）；
- 新增 `AGENT_MODE`（grind/explore，缺省 grind）、`AUTO_START_MUD`、`CHAR_ID/CHAR_PASSWORD` 可选注入；
- 新增 grind 参数区（阈值集中管理）：`KEE_DISENGAGE=40, KEE_RESUME=85, WIMPY=20, FOOD_FLOOR=40,
  WATER_FLOOR=40, SCORE_POLL_SEC=90, CHECKPOINT_SEC=60, STALL_ALARM_MIN=30, ...`。

**state.py（改）** 新增字段：
```python
char_status: dict   # {id,name,exp,potential,gin,gin_max,kee,kee_max,sen,sen_max,
                    #  food,food_max,water,water_max,force,money,location_node,
                    #  skills:{}, wounded:bool, family:str, updated_at:float}
milestone: dict     # {id:"M1", params:{}, started_at}
exp_history: list   # [[ts,exp],...] 截尾保留最近 500 点
credentials: dict   # 运行时引用, 持久化在 credentials.json
escalation: dict    # 例程升级上下文 {routine,reason,detail,room,attempts}
exit_reason: str    # none/reconnect/stop/fatal/goal_reached  (替代三个 bool, 旧字段过渡期保留)
counters: dict      # {deaths,quests_done,quests_skipped,reconnects,llm_failures}
```

**persistence.py（新）**
```python
def save_checkpoint(state: dict, path=CHECKPOINT_FILE) -> None   # 过滤掉 client/llm/future 等对象字段; tmp+os.replace 原子写
def load_checkpoint(path) -> dict | None                         # 校验 version 与 json 完整性, 损坏则回退 .bak
def append_progress(ts, exp, rate, milestone, deaths, quests) -> None  # logs/progress.csv
```
调用点：planner 每次进出、例程内部周期、SIGTERM 处理器、致命异常路径。

**agent.py（改）**
- 启动：`load_checkpoint()` 合并入 initial state；signal.signal(SIGTERM/SIGINT) → 置 `stop_requested`（global），例程与节点轮询；
- `compiled_graph.invoke(state, config={"recursion_limit": 1000})` + 捕获 GraphRecursionError → 记日志按 reconnect 处理；
- 外层 except 区分 `LLMFailure`（退避 60s 重试）与未知异常（保存 checkpoint + 退避重启）；
- 删除阶段1"环境识别"在 grind 模式下的执行（环境就是已知 MUD），explore 模式保留原行为。

**run_agent.sh / stop_agent.sh（改）**
- watchdog 循环：崩溃自动重启，1 小时内 >6 次则停止并写 ALERT 文件；日志按天轮转；
- stop: TERM → 最多等 30s → KILL；
- 启动前自检：MUD 端口可达？world_map.json/npc_index.json 存在？AGENT_MODEL 已设？

### 4.2 P1 mud/protocol.py（新）—— 字节级收发与文本规整 [依赖 P0]

```python
class MudIO:
    def __init__(self, sock_client): ...        # 包装现有 SocketClient, 内部持 bytes 缓冲 + utf-8 增量解码器
    def drain(self, quiet=0.3, deadline=8.0) -> str
        # 连续 recv 直到静默 quiet 秒或到 deadline; IAC 字节级剥离; 增量解码(不丢半个汉字);
        # 自动分页: 检测 "== 未完继续" → 发空行 → 续读拼接(循环, 最多 30 页)
    def send(self, cmd: str) -> bool            # 失败抛 SocketLost
    def request(self, cmd, expect: list[re.Pattern] | None, timeout=8.0) -> MatchResult
        # send + drain + 匹配; 不匹配返回原文供上层判断
class SocketLost(Exception): ...
```
observe.py 改为复用 MudIO.drain()（LLM 环路也享受 drain+分页合并），connection_manager 保留连接管理职责。

### 4.3 P1 mud/profile.py（新）—— ES2 文本协议知识 [无依赖，可并行]

全部为纯函数 + 预编译正则，零 IO（好测）：
```python
parse_hp(text) -> dict          # 气 X/Y(Z%) 内力 X/Y 食物 X/Y 饮水 X/Y 潜能 N 经验 N
parse_score(text) -> dict       # 实战经验/杀气/师承/姓名id
parse_room(text) -> RoomView    # 房名(首行)/出口表/可见对象(中文名+权重)  — 容忍战斗噪声行
parse_quest_grant(text) -> {target_cn, limit_sec} | None   # 含中文数字时限反解析 + "就凭你这种小角色"拒绝识别
detect_events(text) -> list[Event]
    # COMBAT_HIT/WE_DODGE/SKILL_IMPROVED("你的「X」进步了")/FIGHT_REFUSED("并不想跟你较量")
    # OPPONENT_DOWN("倒在地上"/"无法战斗")/DEATH("你死了"等)/GHOST/FLEE("慌里慌张往")
    # QUEST_DONE("恭喜你！你又完成了一项任务")/REWARD(奖励了…点实战经验)/AGGRO(向你攻击)
    # PAGER/LOGIN_PROMPT 系列/TAKEOVER_PROMPT/PASSWORD_ERROR/IDLE
cn_number(s) -> int             # 三分二十秒 → 200
cmd.fight(id,n=None) / cmd.kill(id) / cmd.go(dir) / cmd.surrender() / ...   # 命令构造器(防注入: id 白名单字符)
```

### 4.4 P1 tools/ 离线资产生成（新）[无依赖，可并行]

**tools/build_world.py**：fork map_generator 逻辑，SEARCH_DIR=[mudlib/d, mudlib/u]，修 PROJECT_ROOT，
丢弃解析失败节点（落 warnings 报告），输出 `data/world_map.json`；
自检：BFS 验证 inn→god2、inn→school2、gate→inn1 可达并打印路径；输出重名房间统计。

**tools/build_npc_index.py**：扫描全 mudlib 房间 `set("objects")` + NPC 文件 `set_name/skills/exp/wield/attitude/can_speak`，
计算 `dp_est = dodge³/3 + exp`、`ap_est = max(攻技)³/3 + exp`、armed=是否 wield；
输出 `data/npc_index.json`（结构见 §2.1 Q4）。
派生两份运行配置（人工 review 后入库）：
- `data/spar_ladder.json`：按 dp_est 升序的徒手 can_speak 候选 + 适用 exp 区间；
- `data/quest_whitelist.json`：qlist1000~22000 目标 ∩ npc_index 可定位 ∩ 路线长度/时限可行 ∩ ap_est 可击杀，
  含 {target_cn, kill_id, room_node, est_round_trip_sec, min_exp, max_exp}。

### 4.5 P2 mud/world.py（新）[依赖 4.4]

```python
class World:
    def __init__(self, map_path, npc_index_path): ...
    def find_path(self, from_node, to_node) -> list[(dir, node_id, room_label)]   # BFS; 黑名单房间加权/禁行
    def node_by_label_near(self, label, last_node) -> int | None                  # 重名房间用邻近性消歧
    def locate_npc(self, cn_name) -> list[NpcSite]
    ANCHORS = {"inn":..,"square":..,"school2":..,"schoolhall":..,"god2":..,"herbshop":..,"death_gate":..,"death_inn1":..,"snow_temple":..}
    DANGER = {hummock_id: "run_through", d_wiz_nodes: "forbidden", ...}
```

### 4.6 P2 例程框架 + 图集成 [依赖 4.2/4.3/4.5]

**mud/routines/base.py（新）**
```python
@dataclass
class RoutineResult:
    outcome: str          # completed/failed/escalate/reconnect/stopped/goal_reached
    detail: str
    state_updates: dict   # char_status/exp_history/counters 等增量

class RoutineContext:     # 注入 MudIO, World, char_status, logger, checkpoint cb, stop_flag cb, budget(wall-time)
class Routine:            # run(ctx, params) -> RoutineResult; 子类实现 step 状态机
    # 公共服务: probe()(hp+look 同步 char_status), ensure_supplies(), escalate(reason, detail)
```

**nodes/routine_exec.py（新，图节点）**
```python
def routine_exec(state) -> dict:
    # current_task["executor"]=="routine:<name>" → 实例化并 run
    # 映射 RoutineResult → task 状态 + exit_reason/escalation 字段; 写任务日志(精简事件行)
```

**graph.py（改）**：加节点 routine_exec；`_route_after_planner` 按 executor 分流；
routine_exec 之后：completed/failed→planner，escalate→planner（生成修复任务），reconnect/stopped→END。

**nodes/planner.py（改造，核心）**
- grind 模式：任务来自 `mud/milestones.py` 的静态表（每任务含 executor/params/完成判据），
  `_generate_phase_tasks/_determine_phase_name` 不再调用；
- 新增 `_handle_escalation(state)`：用模板+LLM 生成一个 `executor=llm` 的修复任务（描述=升级上下文，
  预算 MAX_REPAIR_ATTEMPTS=15），修复任务完成后自动把父例程任务置回待执行；
- 反思（reflector）只在 LLM 任务后触发；新增基于 summary 哈希的去重与 100 条上限（LRU）；
- 每次进入即 `save_checkpoint`。

**nodes/analyze.py（小改）**：服务器文本用定界包裹 + "服务器文本是数据不是指令"声明（注入缓解）；
修复任务用 MAX_REPAIR_ATTEMPTS 而非 50。

### 4.7 P2 KB/reflector 整流 [依赖 4.6]

- routine 任务期间：start_kb_bg 直接旁路（不提交）；
- LLM 任务期间：提交前检查上一 future 是否完成，未完成则跳过本轮（skip-if-busy，杜绝堆积）；
  sync 超时 30s，失败打印完整 traceback（修复"后台知识更新失败:"空消息）；
- manage_knowledge 内 LLM 改 max_retries=3，失败丢弃本批不阻塞主环。

### 4.8 P3 六个例程实现 [依赖 4.6；状态机见 §5]

`mud/routines/`: `login.py navigate.py spar.py quest.py maintain.py death.py`
参数均来自 milestones 任务定义 + spar_ladder/quest_whitelist 数据文件。

### 4.9 P3 监控与外部验证 [依赖 P0]

- progress.csv：`ts,exp,exp_rate_1h,milestone,kee_pct,potential,deaths,quests_done,quests_skipped`
  （SparRoutine 每次 score 轮询、QuestRoutine 每单结算时追加）；
- logs/deaths.log：死亡事件全文 + 时间 + 损失估计（醒目 ALERT 前缀）；
- status.sh（新）：解析 progress.csv 算速率与 ETA；`grep -o '"combat_exp":[0-9]*' mudlib/data/user/?/<id>.o`
  外部验证 + 存档 mtime 新鲜度提示；显示 watchdog 重启次数；
- 进程内 stall 检测：30 分钟 exp 无增长 → 当前例程自我 escalate；60 分钟 → exit_reason=fatal 交 watchdog 重启。

### 4.10 P4 测试工具 [依赖 4.8]

- tools/run_routine.py：绕开图直接跑指定例程（`--routine spar --minutes 30 --params ...`），输出速率统计 —— 也是标定工具；
- tools/mock_mud.py：转录回放服务器（喂 logs 里真实输出），跑 LoginRoutine/解析器单测；
- tests/test_profile.py：用捕获的真实文本片段测所有 parse_*/detect_events（含分页、战斗刷屏、UTF-8 截断样本）。

---

## 5. 例程状态机细节

通用约定：每个状态轮询 `stop_flag`（优雅退出）；MudIO 抛 SocketLost → 立即返回 outcome=reconnect
（重入后从 PROBE 收敛）；所有例程开局都是 PROBE（hp + look 同步 char_status 与定位）；
escalate 一律附带 {room, 最近 30 行原文, 已试次数}。

### 5.1 SparRoutine（参数：target_pool[], spar_room, exit_when{exp≥X 或 minutes≥Y}）

状态：`PROBE → PICK → ENGAGE → MONITOR → DISENGAGE → RECOVER → (PICK | PERIODIC)`

| 状态 | 动作 | 转移 |
|---|---|---|
| PROBE | hp+look；不在 spar_room → 内嵌 Navigate；wimpy 未设则 set wimpy 20 | →PICK |
| PICK | 从 look 结果按轮换序选 pool 内目标（编号寻址 fight id N，验证失败则回退轮换法） | 有目标→ENGAGE；全员不应战→ROTATE_WAIT |
| ENGAGE | cmd.fight(id)；期待交战文本 | 交战→MONITOR；FIGHT_REFUSED→标记该目标冷却 120s→PICK；"没有这个人"→PROBE |
| MONITOR | drain 循环；每 8s 一次 hp（轻量），每 SCORE_POLL_SEC 一次 score+progress.csv+checkpoint；统计 SKILL_IMPROVED 事件密度 | kee%<KEE_DISENGAGE→DISENGAGE；OPPONENT_DOWN(对方晕)→PICK；对方变 is_killing/受 wound（armed 误入）→DISENGAGE(flee 模式)+将目标拉黑；被第三方 AGGRO→DISENGAGE(flee)→escalate("遭遇主动攻击")；DEATH→返回 failed(death)（由 planner 派 DeathRecovery）；30 分钟 exp 零增长→escalate("速率异常") |
| DISENGAGE | 常规：surrender → 确认脱战；flee 模式：按 world 给的安全方向 go，失败换向×3 | 脱战→RECOVER；逃跑后→PROBE(重定位) |
| RECOVER | 有 force 技能且内力≥20: exert recover 循环；否则等待自然恢复；期间按需 learn（气>70% 且 潜能-已用>0 且技能<cap，master 在本房或邻房才做）、eat/drink（food/water<FLOOR）、save | kee%≥KEE_RESUME→PICK；食物耗尽且无补给→内嵌 Maintain；超 10 分钟未恢复→escalate("恢复异常") |
| PERIODIC（MONITOR 内嵌） | 评估阶梯：连续 5 分钟攻击通道事件≈0 且我方 exp>目标适用上限 → 切换 pool 下一档；下一档不存在→escalate("梯子断档") | — |
| 退出 | exit_when 达成→completed；预算 wall-time 用尽→completed(带统计) | — |

边界已覆盖：目标不在(PICK→PROBE)、目标晕倒(→PICK)、被第三方攻击(flee+escalate)、恢复等待(RECOVER)、
食物耗尽(→Maintain)、迷路(PROBE 的 Navigate 失败→escalate)、分页(MudIO 自动)、socket 断开(SocketLost)、
MUD 重启(=SocketLost+外层重连)、死亡(→failed→DeathRecovery)。

### 5.2 QuestRoutine（参数：whitelist, base=god2, exit_when）

状态：`PROBE → GOTO_GIVER → REQUEST → RESOLVE → TRAVEL → HUNT → KILL → LOOT → RETURN → REQUEST`
　　　旁路：`SKIP_WAIT`（等待超时）

| 状态 | 动作 | 转移 |
|---|---|---|
| PROBE | hp/score；exp≤1000→直接 failed("未达任务门槛")；补给检查 | →GOTO_GIVER |
| GOTO_GIVER | Navigate→god2（含卧龙岗穿越协议 §5.3） | 到达→REQUEST；导航失败→escalate |
| REQUEST | 发 quest；解析三种回应：新任务/被拒(小角色)/已有任务(usr quest 状态文本) | 新任务→记录 t0=now、解析目标与时限(中文数字+qlist 静态表双保险)→RESOLVE；已有未过期任务→按剩余时间进 SKIP_WAIT 或恢复执行(重入场景，目标仍在白名单→TRAVEL)；解析失败×3→escalate |
| RESOLVE | whitelist 查 target_cn → kill_id/room/est_time；评估：白名单内 且 est_round_trip<limit×0.7 且 ap_est 我方占优 | 通过→TRAVEL；不通过→SKIP_WAIT（计 quests_skipped） |
| TRAVEL | Navigate→目标房 | 到达→HUNT；途中死亡→failed(death)；导航失败→SKIP_WAIT(放弃本单)+escalate 累计 |
| HUNT | look 找 kill_id | 在场→KILL；不在→邻房搜索≤3 房→仍无则原地等 respawn（每 20s look，截止 t0+limit-回程时间）→超时→SKIP_WAIT |
| KILL | cmd.kill(id)；MONITOR 同 Spar 但真打模式：kee%<35% 或 eff_kee 持续下降过快→go 撤退→放弃本单→SKIP_WAIT；目标被打晕未死→补刀(kill 继续) | QUEST_DONE+REWARD 事件→解析所得 exp→LOOT；目标死了但无 QUEST_DONE(超时杀)→LOOT(只捡钱)→SKIP_WAIT 等新单 |
| LOOT | get coin from corpse / get all | →RETURN |
| RETURN | Navigate→god2 | →REQUEST；周期性 progress.csv+checkpoint |
| SKIP_WAIT | 等待 max(0, t0+limit-now)+5s；期间原地小循环：恢复/learn/吃喝；若 base 附近有 pool 目标可切磋则内嵌 Spar(限时) | 计时到→REQUEST |
| 退出 | exit_when→completed；连续 5 单导航/击杀失败→escalate；DEATH→failed(death) | — |

附加规则：tfinished 升档后若新档目标白名单覆盖率<50%，允许策略性连续 SKIP 把档位拉回（F7）；
死亡后 24h 内该目标进黑名单（§2.2-9）。

### 5.3 NavigateRoutine（被复用的子例程）

`LOAD_PATH → STEP → VERIFY → (STEP… ) → ARRIVED`，关键协议：
- VERIFY：每步后 drain 找新房名（首行）与期望节点 label 比对（容忍战斗噪声与重名：用"期望节点"而非全图匹配）；
- 失配 → look 重验 → 仍失配进 RELOCALIZE：按 look 房名+出口指纹在邻域 2 跳内匹配节点 → 命中重算路径；
  3 次失败 → escalate("迷路")；
- 危险房协议（hummock）：进入前确认 kee>80%，连发 `southup` `southdown`（或反向）不在中间 look，
  穿越后 VERIFY + hp 检查，被缠住（仍在战斗）→ 继续朝出口方向 go（go 即逃跑）×3 → 失败 escalate；
- "什么？"（非法方向）→ 不臆测，立即 look 重定位（针对观察报告里的位置幻觉问题）。

### 5.4 LoginRoutine

`BANNER(您的英文名字) → [新号: 名字→y确认→中文名→密码×2→email→性别 | 老号: 名字→密码 | 接管: y]
→ 入世确认(出现房间描述/score 可解析) → set wimpy → save`
- 凭据：首次随机生成（id 3-12 英文小写、密码 12 位随机）写 credentials.json；
- 密码错→**绝不猜**（观察报告教训），换 id 重建或 escalate；连续 3 次建号失败→fatal；
- 死号恢复：登录后若 GHOST 状态→直接转 DeathRecovery。

### 5.5 MaintainRoutine（被 Spar/Quest 内嵌或独立任务）

顺序检查并修复：water/food < FLOOR → 找最近 vendor（inn 店小二/云镇店铺）buy+eat/drink（钱不够→inn 捡 coin/报告）；
wounded → herbshop 买金疮药 apply；潜能可用且技能<cap → learn 循环（气预算 50%）；save；返回。

### 5.6 DeathRecoveryRoutine

`确认鬼状态(score/hp 异常+死亡文本) → Navigate(death 区: gate→…→inn1, 用 world 图, d/death 已在图内)
→ ask <id> about 回家 → 验证复活(到 snow_temple) → RECOVER 到满 → 记 deaths.log+counters → completed`
失败路径：找不到 inn1/ask 无效×3 → escalate（LLM 自由探索修复，死亡区很小）。

---

## 6. 测试与验收

### 6.1 单元/离线（无 MUD）
- test_profile：全部解析器对真实转录样本（含分页、UTF-8 截断、战斗刷屏、登录全流程）；
- test_world：BFS 路径、重名消歧、危险房标注；mock_mud 回放跑 LoginRoutine 全分支（新建/老号/接管/密码错）。

### 6.2 受监督试跑（MUD 原版，开发者在场）
1. `startmud` → tools/run_routine.py 逐例程验证（login→maintain→navigate(含穿山)→spar 30min→quest 5 单）；
   首批现场验证项：`fight trainee 2` 编号寻址是否可用、穿山实际受击伤害、qlist 时限文本样本；
2. **速率标定（M1 门）**：trainee/李火狮 各 30~60min，记录 exp/h、事件分布、占空比 → 写
   docs/calibration.md；判据：综合速率 ≥1500/h 才进长跑，否则先调策略（多敌/armed+药/任务配比）；
3. 混沌测试：切磋中 kill -9 agent → 重启 → 2 分钟内恢复原任务且无重复反思/无凭据丢失；
   任务中 stopmud→startmud → 重连接管成功；拔 KB（模拟 LLM 失败）→ 例程不受影响。

### 6.3 正式长跑（原版判据 + 监控）
- 前置：`git -C /home/wind/project status` 干净、config.ES2 heartbeat=1000、新角色从 0 开始、
  AGENT_MODEL 设好、watchdog 启动；
- 监控：status.sh 随时可查（速率/ETA/存档值/重启数/死亡数）；ALERT 条件（死亡、30min 停滞、
  重启超频、LLM 连续失败）写独立告警日志；
- **达标判定（双重验证）**：①agent 发 `score` 解析 实战经验 ≥100,000（日志留痕）→ 发 `save`；
  ②外部 `grep '"combat_exp"' data/user/<x>/<id>.o` ≥100,000 且 mtime 在 5 分钟内；
  ③归档 progress.csv 全曲线 + deaths.log + watchdog 日志，证明无人值守（无 stdin 介入）。
- 度量定义：exp 速率 = progress.csv 滑动 1h 窗口斜率；ETA = (100000-当前)/速率。

---

## 7. 风险清单（按 概率×影响 排序）

| # | 风险 | P×I | 缓解 |
|---|---|---|---|
| 1 | 经验速率不达标，1~3 天跑不完（F4/F5 梯子断档、甜点漂移） | 高×高 | M1 标定门+自动阶梯(npc_index)+备选加速器(多敌切磋/带药武装陪练/任务配比)+及时上报修正 ETA；接受延长跑而非降级目标 |
| 2 | 长时无人值守的故障累积（断连/LLM 故障/JSON 失败/KB 堆积——均已实测发生过） | 高×中 | P0 全套：max_retries+硬出口、checkpoint、skip-if-busy KB、watchdog 限频、stall 死人开关、SIGTERM 优雅退出 |
| 3 | 死亡螺旋：真打任务目标超评估/穿山被截/armed 误判 → -10%exp 反复 | 中×高 | 战力预检(ap/dp 估值)+kee 35% 弃单+穿山协议+死亡后降档与目标拉黑+deaths.log 告警；徒手切磋为安全基线(F14) |
| 4 | 地图/NPC 索引静态分析与运行时不符（动态房间、随机出口、NPC 漂移） | 中×中 | VERIFY/RELOCALIZE 协议兜底+索引只当先验(HUNT 有搜索与超时)+解析失败一律 escalate 给 LLM 而非硬猜 |
| 5 | 任务白名单覆盖不足 → SKIP 等待吃掉吞吐 | 中×中 | 等待期不空转(原地切磋/learn)+覆盖率统计进 progress+迭代扩名单+策略性降档(F7) |
| 6 | UTF-8 截断/Telnet 噪声导致解析漏判（现有 receive 的真实缺陷） | 中×中 | MudIO 字节缓冲+增量解码+IAC 剥离；解析器用真实转录做回归 |
| 7 | LLM API 长故障窗口 | 中×低 | 例程不依赖 LLM 可继续刷；planner 侧退避等待；升级队列积压超阈值才停 |
| 8 | checkpoint/凭据文件损坏 | 低×高 | 原子写+.bak 轮换+启动校验回退 |
| 9 | 接管/登录边界态（崩溃后旧连接残留、重名、改密） | 低×中 | LoginRoutine 全分支+mock 回放测试+绝不猜密码 |
| 10 | 服务器文本注入 LLM 提示 | 低×低 | 例程期 LLM 不读原文；analyze 定界+声明；命令构造器白名单字符 |

---

## 8. 实现顺序总览（依赖图）

```
P0 工程地基(llm/config/state/persistence/agent/scripts)        ← 第一优先，半天级
P1 mud/protocol.py + mud/profile.py + tools/(world,npc_index)   ← 三者可并行
P2 world.py → routines/base.py + routine_exec + graph/planner 改造 + KB 整流
P3 routines 六件套(login→navigate→maintain→spar→quest→death) + 监控/status.sh
P4 测试工具 + 标定(6.2) → 修正参数 → 正式长跑(6.3)
```
里程碑数据文件（spar_ladder/quest_whitelist）在 P4 标定后定稿，代码与数据分离保证调参不改码。
