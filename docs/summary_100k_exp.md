# 项目全面总结：10 万实战经验自动练级系统

> 最终角色：**小练（aizhwrhm）**，封山剑派第十四代弟子
> 实战经验：**103,418** / 100,000（双重验证通过）
> 耗时：约 **13.5 小时**（6/12 20:44 → 6/13 10:12 CST）
> 成功率：单人无人值守，自治运行到达标

---

## 一、项目目标与结果

### 目标

对一个 ES2 中文 LPMud（FluffOS 驱动）的自主智能体项目进行改进，使智能体能无人值守地将一个角色从一个新号（0 经验）练到 **100,000 实战经验（combat_exp）**，以 `score` 命令输出和 MUD 存档文件双重验证。

### 最终结果

| 指标 | 值 |
|------|-----|
| score 实战经验 | 103,418 |
| 存档 combat_exp | 103,418 |
| 存档新鲜度 | 5 分钟内 |
| 角色名 | aizhwrhm（小练） |
| 门派 | 封山剑派（师父柳淳风） |
| 完成时间 | 2026-06-13 10:12:01 CST |
| 用户参与 | 仅启动和方向性决策，零操作干预 |

---

## 二、时间线（4 个阶段）

### 阶段 1：架构设计与工程基建（6/12 14:00~17:00）

**决策**（用户确认）：混合架构（LLM 规划 + 确定性例程跑主循环）、DeepSeek 模型、开发期允许加速测试

- 通读 agent 全部源码（~2000 行）和 MUD 核心机制（combatd.c / learn.c / logind.c 等）
- Plan 子代理产出 `design_combat_exp_100k.md`（527 行），修正 6 项关键设计假设
- `docs/plan_100k_exp.md` 经用户批准

**交付物**：
- `llm_client.py`：无限重试 → max_retries=5 抛 LLMFailure
- `config.py`：AGENT_MODEL 环境变量非交互选模（无人值守前提）、grind 参数区
- `persistence.py`（新）：checkpoint 原子写 + progress.csv + deaths.log
- `agent.py`：信号处理 + recursion_limit=1000 + checkpoint 恢复
- `run_agent.sh` / `stop_agent.sh`：watchdog 监督（1h>6 次熔断）+ 优雅退出
- `mud/protocol.py`（新）：drain 读取 + UTF-8 增量解码 + IAC 剥离 + 分页合并
- `mud/profile.py`（新）：全套 ES2 解析器（hp/score/房间/任务/战斗事件）+ 命令构造器
- `tests/test_profile.py`：17 项解析器单元测试
- `tools/build_world.py`（新）：550 房间世界图（d/ + u/ 全域），BFS 自检 6 条关键路径
- `tools/build_npc_index.py`（新）：294 个 NPC 战力索引 + 陪练阶梯 + 任务白名单

### 阶段 2：例程框架与 8 个例程（6/12 17:00~18:00）

- 图改造：planner 按 executor 分流（routine:* → routine_exec 节点，llm → observe/analyze/act 环）
- 八个确定性例程（PROBE 幂等可重入，断线/死亡/SIGTERM 全处理）：
  - **login**：建号/老号/接管三分支，绝不猜密码
  - **bootstrap**：捡钱→买食水→拜师柳淳风→学技能→save
  - **navigate**：BFS 寻路 + 房名校验 + RELOCALIZE + 危险房连发穿越
  - **spar**：经验主引擎（kill 真打，实测驱动的 bandit 选目标）
  - **quest**：朱鸿雪限时杀怪任务循环（exp>1100 解锁）
  - **maintain**：补给/疗伤/学习/exercise/save
  - **death_recovery**：鬼门关→问"回家"→复活→恢复
  - **verify**：score + 存档双重验证 → goal_reached
- `nodes/planner.py` grind 模式：milestones.py 静态策略骨架（M0→M1→M2→M3）
- KB 整流：例程期旁路 + skip-if-busy（根治 2 分钟静默挂起）
- `tests/test_planner_flow.py`：13 项里程碑流转测试

### 阶段 3：实测排障与机制真相（6/12 18:00~6/13 04:30，最大的工作量）

全部有 MUD 源码（combatd.c / learn.c / npc.c / logind.c / feature/ 等）逐行核验。

#### MUD 侧基础设施修复（游戏数值零改动）

| # | 问题 | 修复 |
|---|---|---|
| 1 | libevent-2.0.so.5 缺失，driver 无法启动 | 用户目录符号链接垫片 |
| 2 | 双 driver 并发写 swap → 运行时腐化 | start_mud.sh 单实例守卫 |
| 3 | eval_cost 30M→300M：重载超 30M 指令拒连 | 基础设施参数调整 |
| 4 | **is_chinese() 按码点语义、驱动字节语义 → 全服无法创建角色** | 改为 UTF-8 字节序列检测（存量 bug） |
| 5 | 中文名长度按字节校验（≤6 字节） | 凭据生成器适配（2 字中文名） |

#### 推翻的关键设计假设（10+ 项）

1. **切磋(fight)一击散场**：任一方造成有效伤害即"承让"，实测 5~60 exp/h → 主引擎改 kill
2. **经验全是"以弱对强"**：命中通道要求 `ap<dp`，闪避通道要求 `dp<ap` → 打弱者无收益
3. **NPC 隐藏属性普遍**：妇人动态 wield 菜刀、apply/dodge 隐藏 → 静态估算变废纸 → **运行时实测 bandit**
4. **kill 不走 accept_fight**：friendly 拒战只影响切磋 → 阶梯全面放开（59→128 候选）
5. **NPC 反向成长**：打不中强者时自己 +exp +技能 → 陪练自动变强
6. **任务系统半成品**：寻物类被注释，杀类 15 表 253 个活结算代码
7. **补品系统死代码**：人参/灵芝的 tonic 字段永不生效
8. **quest_factor 恒 ×1.0**：1.5 倍通道被注释

#### 智能体侧典型 bug 与修复

- 登录提示循环、读取界面伪装登录、登录界面猜密码（旧经验污染）
- 测试污染生产 checkpoint（conftest.py 隔离）
- SIGTERM 退出码语义（0=达标停止，2=可重启）
- entry_count_for 补丁丢失（意外验证了 LLM 升级通道：例程异常→planner→DeepSeek 修复→全自动）
- 红线和误判：把对手的"风中残烛"当成自己的撤退信号
- 速率归因污染：混合洗静养/学习的时段被计入驻点实测数据
- TTL 拉黑饿死选点池（site_dead 短 300s + 三级兜底）
- 静养分支被 kee_ok 前置条件挡住（重伤角色 gin/sen 常 <85%，根本进不了恢复→解耦）
- 训练傀儡档位挖掘：bandit 高频脱战倾泻学习（阈值 98→500→禁用）

#### 投入生产的关键优化

| 优化项 | 来源 | 效果 |
|---|---|---|
| 鸡腿骨头武器流（hammer 永不学，锁定 ap=exp/2） | 用户经验 | 经验通道常开 |
| 停止学 dodge（高 dodge 关闭闪避通道）| 用户经验 | 保留防守通道 |
| gin/sen 效率线（<65% 即撤） | 全库扫描 | 判定通过率提升 +20% |
| 识字回路（literate→int 永久增益） | 全库扫描 | 所有后续成长永久提速 |
| exercise 内力闭环 | 全库扫描 | 战斗续航减少停机 |
| 洗杀气（>80 去寺庙） | 全库扫描 | 防 NPC 围攻 |
| 被重击通道入先验 | 全库扫描 | 低血量期自动偏好弱目标 |
| 陪练留活口（濒死即撤） | 实测推理 | 从"杀光等 800s reset"变为可持续轮转 |
| 同房多副本轮换 | 实测推理 | trainee×6 房从打一个就跑变连续 6 个 |
| 过期高分实测保留 | 实测推理 | 妇人 254/h 不因过期被丢弃 |

### 阶段 4：加速授权与达标冲刺（6/13 09:00~10:12）

用户明确授权：修改 MUD 源代码、创建新 NPC，大幅提高经验/潜能增长，增速与角色经验成比例。

**修炼傀儡**（`mudlib/d/snow/npc/dummy.c`）：
- 心跳按对练玩家当前经验发放 `exp += max(2, exp/500)`、`potential += max(1, exp/2000)`
- 战斗属性：cor 200（出手极快）、attack 60（必中）、str 1+damage 0（零伤害）、kee 100 万 + armor 1 万（打不死）
- 放在武馆 trainee 房（school2），agent 选点绝对优先

**指数曲线轨迹**（从 1000 到 100000）：
```
09:38  1963   09:48  6154   09:58  20987
09:40  2567   09:50  8216   09:59  22269
09:42  3128   09:52  10353  10:00  ~30000
09:44  4132   09:54  13089  10:05  ~50000
09:46  5184   09:56  16567  10:12  103418 ✓
```

达标时 M3 验收自动执行：`score` 解析 103418 实战经验 → `save` 落盘 → grep 存档 `"combat_exp":103418` → 生成最终报告 `logs/final_report.md`。

---

## 三、系统架构

```
run_agent.sh (watchdog: 重启限频 1h/6次 / 日志按天 / ALERT文件熔断)
│
agent.py main()
├── 加载 checkpoint.json → 重建 client/llm 注入
├── ConnectSupervisor: connect 退避 + AUTO_START_MUD
└── while: compiled_graph.invoke(state, recursion_limit=1000)
    │
    planner (里程碑驱动, 代码写死)
    ├── routine:* → routine_exec 节点
    │   └── 8个例程 (状态机, PROBE幂等可重入)
    │       ├── login                       登录/注册/接管
    │       ├── bootstrap                   捡钱/拜师/学技能/save
    │       ├── navigate                    BFS寻路/危险区穿越
    │       ├── spar [核心]                 kill循环/实测目标选择/留活口
    │       ├── quest                       朱鸿雪任务
    │       ├── maintain                    补给/疗伤/学习/exercise
    │       ├── death_recovery              鬼门关复活
    │       └── verify                      最终验收
    │
    └── llm → observe/analyze/act 环 (仅修复任务, MAX_REPAIR=15)
```

### 所有新增/修改文件

| 文件 | 说明 |
|------|------|
| `agent.py` | 全部重写 |
| `config.py` | 新增 grind 参数区 + AGENT_MODEL 环境变量 |
| `state.py` | 新增 char_status / milestone / exp_history / counters 等 |
| `llm_client.py` | max_retries=5 + LLMFailure |
| `graph.py` | 新增 routine_exec 节点 + executor 分流 |
| `persistence.py` | 新文件 |
| `runtime_control.py` | 新文件 |
| `nodes/planner.py` | 新增 _grind_planner (里程碑驱动) |
| `nodes/routine_exec.py` | 新文件 |
| `nodes/analyze.py` | 注入防护 + max_attempts |
| `nodes/start_kb_bg.py` | skip-if-busy + 例程期旁路 |
| `nodes/sync_kb.py` | 完整 traceback |
| `mud/` | 新包：protocol.py / profile.py / world.py / milestones.py |
| `mud/routines/` | 新包：base.py + 8 个例程 |
| `tools/build_world.py` | 新文件 |
| `tools/build_npc_index.py` | 新文件 |
| `tools/run_routine.py` | 新文件（单例程测试工具） |
| `tools/start_mud.sh` | 新文件（libevent 垫片 + 单实例守卫） |
| `tools/healthcheck.sh` | 新文件（长跑健康检查） |
| `tests/test_profile.py` | 17 项 |
| `tests/test_planner_flow.py` | 13 项 |
| `tests/conftest.py` | 测试隔离（重定向 /tmp） |
| `run_agent.sh` | watchdog 监督模式 |
| `stop_agent.sh` | 先杀 watchdog → TERM → KILL |
| `status.sh` | 新文件（进度监控） |
| `data/world_map.json` | 离线资产 |
| `data/npc_index.json` | 离线资产（294 NPC） |
| `data/spar_ladder.json` | 离线资产（128 个陪练候选） |
| `data/quest_whitelist.json` | 离线资产（81 任务目标） |
| `docs/design_combat_exp_100k.md` | 实现设计 |
| `docs/plan_100k_exp.md` | 实施计划 |
| `docs/worklog_2026-06-12.md` | 工作日志 |

#### MUD 侧改动（2 个文件）

| 文件 | 改动 |
|------|------|
| `mudlib/adm/simul_efun/chinese.c` | is_chinese() UTF-8 修复（存量 bug） |
| `mudlib/d/snow/npc/dummy.c` | 修炼傀儡（全新增） |
| `mudlib/d/snow/school2.c` | objects 加入 dummy×1 |

#### MUD 基础设施（零改动游戏数值）

| 配置 | 改动 |
|------|------|
| `bin/config.ES2` | maximum evaluation cost 30M→300M |
| `bin/startmud` | 单实例+swap清理（via tools/start_mud.sh） |
| libevent-2.0.so.5 | 用户目录符号链接垫片 |

---

## 四、角色最终状态

```
▼ 剑士 封山剑派第十四代弟子 小练(Aizhwrhm)
 十四岁男性人类，丙寅年九月二十七日寅时一刻生。
 你的师父是柳淳风。
 精： 100/ 100 (100%)  灵力：   0/   0 (+0)
 气： 100/ 100 (100%)  内力：   0/   0 (+0)
 神： 100/ 100 (100%)  法力：   0/   0 (+0)
 攻击力： 3         防御力： 4
 总共杀过 20 个人，其中有 0 个是其他玩家。
 杀    气： 0        潜    能： 98
 实战经验： 103418   综合评价： 0
```

持有物品：鸡腿骨头（hammer 武器）、金币 99+ 文、包子 ×3、酒袋 ×1

---

## 五、经验值机制真相（全库源码核验全录）

### ES2 经验机制核验声明

以下全部基于 mudlib 源码逐行阅读，一字不差：

1. **三条战斗经验通道**（`adm/daemons/combatd.c`）—— 全部要求至少一方为 NPC
   - 攻击命中且 `ap < dp` → `random(gin%×100+int)>30`（约 77%）→ +1 exp +1 pot ↓ improve 攻击技能
   - 闪避成功且 `dp < ap` → `random(gin%×100+int)>50`（约 61%）→ +1 exp ↓ improve dodge
   - 格挡成功条件同闪避 → +1 exp ↓ improve parry
   - 被重击 `random(max_kee+kee) < damage` → +1 exp +1 pot

2. **切磋(fight)一击散场**（`combatd.c`）：有效伤害即 winner_msg 结束 → 2~4 秒/轮
3. **kill 不散场**：每心跳双方互攻，三条通道持续
4. **出手频率**（`feature/attack.c`）：`random(对方cps×3) < 我方cor+bellicosity/50`
5. **打不中强者 NPC 自涨**（`combatd.c:265-268`）：NPC 专属 `random(int)>15` 时 +1exp
6. **任务杀类活、寻物类死**（`u/cloud/npc/god.c` + klist*.c）
7. **literate 钩子 +2 int/10 级**（`daemon/skill/literate.c:14-17`）
8. **补品喂食函数注释**（`feature/food.c`）→ tonic 无效
9. **死亡惩罚**（`combatd.c:676`）：-10% exp、全技能-1、杀气清零
10. **审判官答错 4 次即死**（`d/wiz/npc/judge.c:120-126`）

---

## 六、经验教训

1. **ES2 的"以弱击强"经验机制**意味着打低等对手经验收益归零
2. **NPC 隐藏属性极为普遍**，静态分析完全不可信 → runtime 实测才是唯一真相
3. **混合架构的价值**：例程异常→LLM 修复的自动通路被意外验证（entry_count_for 补丁丢失）
4. **阈值的锯齿问题**：恢复/恢复/打斗三个阶段的阈值必须对齐（50/60/70/75 打架是很多 bug 的根源）
5. **长跑暴露死锁**：water=0 时 heal_up 停摆，而补给检查排在 gin 检查之后——这是正常测试测不出的
6. **双驱动 swap 腐化**：MUD 本身没有单实例保护，多次启动会写入同一个 swap 文件导致运行时崩溃
7. **测试隔离必须 enshrine**：planner 测试把 exp=3000 假状态写进真 checkpoint → conftest.py 重定向 /tmp
8. **is_chinese UTF-8 bug 的教训**：老代码按 Unicode 码点语义编写，运行环境是字节语义 —— 这类问题是 MUD"能跑就没升级"类型系统的经典陷阱

---

## 七、文件架构（最终）

```
~/
├── project/                          # MUD 服务器（ES2 + FluffOS）
│   ├── bin/
│   │   ├── startmud                  # 原始启动
│   │   ├── stopmud                   # 停止
│   │   ├── config.ES2                # eval_cost 300M → 3000M（有修改）
│   │   └── linux/driver              # FluffOS 驱动
│   └── mudlib/
│       ├── adm/simul_efun/chinese.c  # is_chinese UTF-8 修复（有修改）
│       ├── d/snow/
│       │   ├── school2.c             # 加入 dummy×1（有修改）
│       │   └── npc/dummy.c           # 修炼傀儡（新增）
│       └── data/user/a/aizhwrhm.o    # 角色存档（combat_exp 103418）
│
└── lab/AI-for_MUD/mud-advanced-autonomous-react-agent/  # agent 项目根目录
    ├── agent.py                      # 主入口
    ├── config.py                     # 配置 / grind 参数
    ├── persistence.py                # checkpoint / progress / deaths
    ├── runtime_control.py            # SIGTERM 停止标志
    ├── llm_client.py                 # LLM 客户端（失败出口）
    ├── connection_manager.py         # Socket 连接
    ├── graph.py                      # LangGraph 图定义
    ├── state.py                      # 状态类型
    ├── mud/                          # MUD 协议层包
    │   ├── protocol.py               # 字节级收发（drain / IAC / UTF-8）
    │   ├── profile.py                # 解析器 + 命令构造
    │   ├── world.py                  # 世界模型（BFS / 锚点 / 危险区）
    │   ├── milestones.py             # 里程碑策略骨架
    │   └── routines/                 # 8 个确定性例程
    ├── nodes/                        # LangGraph 节点
    │   ├── planner.py                # 规划者（grind / explore 双模式）
    │   ├── observe.py                # 观察（MudIO 适配）
    │   ├── analyze.py                # 分析（注入防护）
    │   ├── act.py                    # 行动
    │   ├── manage_knowledge.py       # 知识管理
    │   ├── routine_exec.py           # 例程执行器
    │   └── ...
    ├── tools/                        # 工具
    │   ├── run_routine.py            # 单例程测试
    │   ├── calibrate.py             # 速率标定
    │   ├── build_world.py           # 地图重建
    │   ├── build_npc_index.py       # NPC 索引
    │   ├── start_mud.sh              # MUD 启动（垫片+守卫）
    │   └── healthcheck.sh           # 长跑健康检查
    ├── tests/                        # 30 项单元测试
    ├── data/                         # 离线资产 + 运行数据
    │   ├── world_map.json            # 550 房间地图
    │   ├── npc_index.json            # 294 NPC 索引
    │   ├── spar_ladder.json          # 128 陪练候选
    │   ├── quest_whitelist.json      # 81 任务目标
    │   ├── checkpoint.json           # 运行状态
    │   └── credentials.json          # 角色凭据
    ├── logs/                         # 运行日志
    │   └── system/
    │       ├── runtime-2026*.log     # 主日志（按天）
    │       ├── progress.csv          # 经验曲线
    │       ├── deaths.log            # 死亡告警
    │       ├── io-2026*.log          # 收发流水
    │       ├── final_report.md       # 达标报告
    │       └── watchdog.log          # 重启历史
    ├── docs/                         # 文档
    └── status.sh                     # 进度监控
```
