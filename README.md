# mud-advanced-autonomous-react-agent — MUD 高级自主智能体（LangGraph）

> **与 [MUD-ES2](https://github.com/zhtxiii/MUD-ES2) 紧密配合**  
> 本智能体专为 [MUD-ES2](https://github.com/zhtxiii/MUD-ES2) 项目所运行的 MUD 服务端定制开发。
> 智能体可以直接读取 MUD 服务端的 **全部源码**（combatd.c、skilld.c、任务系统等），
> 从而对战斗机制、经验结算、NPC 行为、寻路等做出精确判断，实现有针对性的优化和修改，
> 而非基于猜测或经验推导行事。

基于 LangGraph 的自主智能体。当前版本为 **grind 模式**：以"把一个角色练到
100,000 实战经验"为目标的里程碑驱动混合架构 —— 确定性例程跑主循环，
LLM 只负责规划微调与异常修复。原开放式探索架构保留为 explore 模式。

## 架构（grind 模式）

```
planner(里程碑驱动, 代码为主)
  ├─ routine:* 任务 → routine_exec 节点（确定性例程, 不经过每轮 LLM）
  │    login / bootstrap / navigate / spar / quest / maintain / death_recovery / verify
  └─ llm 任务（例程升级的修复任务）→ observe → analyze → act 循环（LLM 决策）
```

| 组件 | 说明 |
|------|------|
| `mud/milestones.py` | 里程碑策略骨架：M0 立足 → M1 刷到 1100 → M2 任务×陪练双循环 → M3 双重验证 |
| `mud/routines/` | 八个确定性例程（状态机，PROBE 幂等可重入，断线/死亡/停止信号全处理） |
| `mud/profile.py` | ES2 文本协议：hp/score/房间/任务/战斗事件解析 + 命令构造（防注入） |
| `mud/protocol.py` | MudIO：drain 式读取、Telnet IAC 剥离、UTF-8 增量解码、分页自动合并 |
| `mud/world.py` | 550 房间寻路（BFS）+ 危险区策略（卧龙岗连发穿越、d/wiz 禁行） |
| `persistence.py` | checkpoint 原子读写（崩溃恢复）、progress.csv、deaths.log |
| `tools/build_world.py` | 离线重建地图（d/ + u/ 全域） |
| `tools/build_npc_index.py` | NPC 索引：战力估算（含隐藏 apply 检测）、陪练阶梯、任务白名单 |
| `tools/run_routine.py` | 单例程实测工具（绕开图直接跑） |
| `tools/calibrate.py` | 陪练速率标定 |
| `tools/healthcheck.sh` | 长跑健康检查（进程/存档经验/进度新鲜度/告警） |
| `status.sh` | 进度速览：存档经验、速率、ETA、死亡数 |

## 关键机制（combatd.c 等源码核验结论）

- 杀怪本身不给经验；经验来自战斗事件概率 +1 与任务结算
- 切磋(fight)一击定胜负（首次有效伤害即"承让"散场）；kill 真打才有持续回合 → 主引擎用 kill
- **kill 不走 accept_fight**：friendly 拒战只影响切磋，kill 谁都能打 → 阶梯全面放开（128 候选）
- 经验通道全部是"以弱对强"判定：命中(ap<dp, 77%)/闪避(dp<ap, 61%)/被重击(damage 比例)
- 出手频率 = `random(对方cps×3) < 我方cor+杀气/50`：野兽 cps 5~15（人类 10~30）→ 打野兽出手约 2 倍快；野兽不说话不浪费心跳
- NPC 打不中强者时自己 +exp +技能（`!userp` 专属通道）→ 陪练自动成长，长期可持续
- NPC 有隐藏 apply（如妇人 apply/dodge）和动态武装（战斗中 wield 菜刀）→ 静态估算不可靠 → **运行时实测 bandit 选目标**（EWMA 速率库 + 速率底线轮换 + 拉黑 TTL）
- 任务系统为半成品：寻物类交付函数被注释（未实现）；**杀类任务结算有效**（15 张表 253 个全为杀类，exp>1000 解锁）
- 死亡惩罚 -10% 经验 + 全技能-1 → wimpy 40 + 撤退线 + 按血量动态限制攻击者数三重防护
- 学习（learn）有经验封顶 `level³/10 ≤ exp`；优先学内功（max_force 抬 max_kee 上限 + exert recover）和招架
- 金疮药 2000 文（前期买不起）；伤势可自然恢复（气满后 eff_kee +1/tick）

## 运行

```bash
# 一次性：生成离线资产
python3 tools/build_world.py && python3 tools/build_npc_index.py

# 启动 MUD（带 libevent 兼容垫片 + 单实例守卫）
tools/start_mud.sh

# 无人值守长跑（watchdog 自动重启，1小时>6次熔断）
AGENT_MODEL=1 ./run_agent.sh          # 1=DeepSeek 2=Polo
./status.sh                           # 查进度
./stop_agent.sh                       # 优雅停止（TERM→checkpoint→KILL）

# 调试
AGENT_MODEL=1 ./run_agent.sh --fg     # 前台直跑
python3 tools/run_routine.py --routine spar --minutes 10 --no-llm
AGENT_MODE=explore ./run_agent.sh     # 原开放式探索模式
```

## 测试

```bash
python3 -m pytest tests/ -q   # 解析器 + planner 流转（隔离于 /tmp，不污染生产数据）
```

## 日志与数据

| 文件 | 内容 |
|------|------|
| `logs/system/runtime-*.log` | 主日志（按天） |
| `logs/system/io-*.log` | 原始收发流水 |
| `logs/system/progress.csv` | 经验曲线（ts,exp,rate,milestone,...） |
| `logs/system/deaths.log` | 死亡告警 |
| `logs/system/watchdog.log` | 重启历史 |
| `data/checkpoint.json` | 运行状态（崩溃恢复用，原子写+.bak） |
| `data/credentials.json` | 角色凭据（自动生成） |

退出码语义：`0` = 目标达成（watchdog 停止）；`2` = 信号停止/其他（watchdog 重启）。

## 文档

| 文档 | 内容 |
|------|------|
| `docs/design_combat_exp_100k.md` | 实现设计（机制核验、例程状态机、风险清单） |
| `docs/plan_100k_exp.md` | 实施计划（P0~P5） |
| `docs/worklog_2026-06-12.md` | 开发工作日志（排障记录、机制真相、当前状态） |
| `process_flow_issues.md` | 原架构问题分析（历史） |
