# mud-advanced-autonomous-react-agent — MUD 高级自主反应式智能体（LangGraph）

基于 LangGraph 框架的自主智能体，采用**规划者驱动 + 阶段化任务**架构，通过高效的并行执行流实现对远程 Socket 服务的自主探索与交互。

## 核心架构

| 角色 | 模型 | 职责 |
|------|------|------|
| **Planner** | `deepseek-v4-flash` | 宏观阶段划分、任务生成、制定执行计划 |
| **Executor** | `deepseek-v4-flash` | Observe → Analyze → Act 执行循环 |
| **Knowledge Manager** | 后台线程 | 维护长期记忆，跨任务信息检索 |
| **Reflector** | `deepseek-v4-flash` | 分析执行日志，总结经验与技能 |

## 执行流程

```
Planning:  Planner → 分配任务
            ↓
Execution: Observe → Start KB(后台) → Analyze → Act → Sync KB
            ↓                                  ↑
        任务未完成 ─────────────────────────────┘
            ↓
        任务完成/僵局 → Planner (重新规划)
```

## 关键特性
- **零阻塞知识库**：知识更新在后台线程执行，不延迟处理
- **僵局处理**：50 次尝试无进展自动回退 Planner 重新决策
- **垃圾过滤**：自动过滤 Telnet 协商乱码和编译器警告
- **智能重试**：LLM 调用内置重试和 JSON 校验

## 快速开始

```bash
# 安装依赖
pip install langgraph openai

# 配置 API Key（优先读取 DEEPSEEK_API_KEY，也可写入 apikey.txt）
# 可选：通过 DEEPSEEK_MODEL 覆盖模型，默认 deepseek-v4-flash
# 配置服务器地址 (config.py，或 AGENT_TARGET_IP / AGENT_TARGET_PORT)

# 运行
./run_agent.sh

# 如需指定 Python 解释器
PYTHON_BIN=/path/to/python ./run_agent.sh
```

## 日志监控
| 日志 | 路径 |
|------|------|
| 实时日志 | `tail -f logs/system/runtime.log` |
| 规划者 | `logs/planner/history.log` |
| 反思者 | `logs/reflector/reflections.log` |
| 知识管理 | `logs/knowledge/manager.log` |
| 任务详情 | `logs/tasks/` |
