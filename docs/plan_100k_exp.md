# 实施计划：培养 10 万经验玩家

## 目标
改造智能体使其能无人值守地把一个新角色在原版 ES2 MUD 上练到 **combat_exp ≥ 100,000**，并实际执行长跑直到达成（`score` 输出 + 存档文件双重验证）。

## 方案概要
混合架构：**LLM(DeepSeek) 只做规划与异常处理，确定性例程跑刷经验主循环**。
详细设计（机制核验、状态机、风险清单）见 `docs/design_combat_exp_100k.md`。

核心策略（基于 MUD 源码核验）：
- 经验主力 = **切磋**（战斗事件概率 +1）：trainee 起步 → 拜师后李火狮 → NPC 索引自动构建后期陪练阶梯
- 任务循环（朱鸿雪，exp>1000 解锁）主要供给 **potential**（learn 技能用），顺带经验
- 安全基线：只与徒手 NPC 长时间切磋（切不死人），surrender 脱战，`set wimpy 20` 保险，死亡 = -10% exp 必须严防
- 里程碑：M0 立足（建号/捡钱买食水/拜师/learn）→ M1 雪镇切磋到 2000 + 速率标定 → M2 云镇 任务×切磋 双循环到 100k → M3 双重验证

## 实施步骤

### P0 工程地基（无人值守生死线）
- `llm_client.py`：max_retries=5 抛 LLMFailure（替代无限重试），timeout 600→120s
- `config.py`：`AGENT_MODEL` 环境变量非交互选型；grind 参数区（脱战/恢复阈值等）
- `state.py`：新增 char_status / milestone / exp_history / credentials / escalation / exit_reason / counters
- `persistence.py`（新）：checkpoint.json 原子读写（崩溃恢复进度），progress.csv 追加
- `agent.py`：启动加载 checkpoint；SIGTERM 优雅退出；`recursion_limit=1000`（修隐藏 bug：默认 25 导致频繁 GraphRecursionError 被吞）
- `run_agent.sh`/`stop_agent.sh`：watchdog 重启限频 + 启动自检；TERM→等待→KILL

### P1 协议层与离线资产（三者并行）
- `mud/protocol.py`（新）：MudIO —— drain 式读取（字节缓冲+UTF-8 增量解码，修跨块吃半个汉字）、IAC 剥离、分页自动翻页合并
- `mud/profile.py`（新）：ES2 解析器（hp/score/房间/任务/战斗事件/登录提示/中文数字时限）+ 命令构造器
- `tools/build_world.py`（新）：重建地图覆盖 **d/ + u/**（现有 mud_map.json 缺任务区域 u/cloud，QuestRoutine 无图可用）→ `data/world_map.json`
- `tools/build_npc_index.py`（新）：全服 NPC 索引（中文名→房间/英文id/exp/战力估值/是否持武器）→ 派生陪练阶梯 + 任务白名单

### P2 例程框架与图集成
- `mud/world.py`（新）：BFS 寻路 + 关键地点锚 + 危险区（卧龙岗穿越协议、d/wiz 禁行）
- `mud/routines/base.py`（新）：Routine 基类（PROBE 幂等重入 / escalate 升级 / 周期 checkpoint / stop_flag）
- `nodes/routine_exec.py`（新）+ `graph.py`：任务按 executor 分流（routine:* 直达例程节点；llm 走原循环）
- `nodes/planner.py`：grind 模式用 `mud/milestones.py` 静态里程碑表替代 LLM 开放式生成；LLM 收缩为处理例程升级（修复任务预算 15 轮）与僵局决策
- KB/reflector 整流：例程期完全旁路；LLM 任务期 skip-if-busy 防堆积；反思去重封顶

### P3 六个例程 + 监控
- `mud/routines/`：login（含崩溃重连"赶出去(y/n)"接管、绝不猜密码）、navigate（每步房名校验 + RELOCALIZE 防位置幻觉）、spar（选目标→fight→气<40% surrender→恢复期 learn/吃喝→阶梯自动升档）、quest（领单→白名单评估→寻路→kill→等超时跳过不可达单）、maintain（食水/买药/learn/save）、death_recovery（鬼门关复活流程）
- 监控：`logs/progress.csv`（exp/速率/死亡/任务数）、`logs/deaths.log` 告警、`status.sh` 外部验证（grep 存档 combat_exp + ETA）、30 分钟无进展自愈

### P4 测试与标定（MUD 原版，我在场监督）
- 解析器单测（真实转录回放，含分页/UTF-8 截断/战斗刷屏样本）
- 启动 MUD → `tools/run_routine.py` 逐例程实测（login→maintain→navigate 含穿山→spar 30min→quest 5 单）
- **速率标定门**：实测 exp/h ≥1500 才进长跑，否则启用备选（多 NPC 同切/带药打武装陪练/任务配比）并修正 ETA
- 混沌测试：kill -9 后 2 分钟内恢复任务；stopmud→startmud 重连接管

### P5 正式长跑（无人值守，我负责盯到成功）
- 前置确认：MUD 原版（mudlib 无改动、heartbeat=1000）、新角色从 0 开始
- watchdog 后台启动；我在本会话用 Monitor + 定时任务定期检查 progress/告警，异常时介入修复后续跑
- 预计 35~70 小时连续运行（标定后给准确 ETA）
- **达标判定**：score 实战经验 ≥100,000 + 外部 grep 存档 `"combat_exp"` ≥100,000（mtime 新鲜）→ 归档曲线与报告

## 说明
- 开发调试期允许临时加速 MUD（已获许可），标定与正式长跑用原版
- LLM 用 DeepSeek（apikey.txt 已有配置）
- 不改 MUD 游戏内容/数值；agent 仓库的改动不主动 git 提交（如需提交会先问）
