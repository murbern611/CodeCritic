<p align="center">
  <img src="./logo.png" alt="CodeCritic Logo" width="120">
</p>

<h1 align="center">CodeCritic ⚡ — 多智能体代码评审辩论系统</h1>

> 基于 LangChain + LangGraph 的多智能体协作框架，让多个专业 Agent 从不同维度审查代码，通过辩论机制达成共识，输出高质量评审报告。

---

## 目录

- [项目背景](#项目背景)
- [核心架构](#核心架构)
- [工作流程](#工作流程)
- [功能特性](#功能特性)
  - [多智能体辩论机制](#1-多智能体辩论机制)
  - [Diff 感知审查](#7-diff-感知审查)
  - [记忆系统](#2-记忆系统)
  - [Token 消耗追踪](#3-token-消耗追踪)
  - [自定义智能体 Prompt](#4-自定义智能体-prompt)
  - [结构化输出](#5-结构化输出)
  - [Prompt 缓存系统（KV Cache 共享）](#6-prompt-缓存系统kv-cache-共享)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [配置指南](#配置指南)
- [使用示例](#使用示例)
- [技术栈](#技术栈)
- [路线图](#路线图)

---

## 项目背景

传统的代码审查依赖人工，耗时长、标准不统一。现有的 AI Code Review 工具大多是**单 Agent 模式**——一个模型从头看到尾，视角单一，容易遗漏问题。

CodeCritic 采用 **多智能体辩论架构**：

-  **安全专家** — 寻找漏洞、注入风险、敏感信息泄露
-  **性能专家** — 分析复杂度、瓶颈、缓存机会
-  **代码风格专家** — 检查规范一致性、可读性、最佳实践
-  **正确性专家** — 寻找逻辑错误、边界条件、竞态条件
-  **架构专家** — 评估设计模式、耦合度、扩展性

各 Agent 独立审查 → 检测分歧 → **必要时辩论** → 仲裁生成最终报告。

---

## 核心架构

```
                    ┌──────────┐
                    │  __start__│
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │  parse   │  ← 解析输入代码
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │  review  │  ← 并行调用所有审查 Agent（缓存预热）
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │  judge   │  ← 检测分歧 + 定位冲突 Agent 对
                    └────┬─────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
      ┌──────────────┐      ┌──────────┐
      │  debate(N)    │      │  skip    │  ← 条件路由
      │ 仅冲突 Agent   │      └────┬─────┘
      │ 互看观点并辩护  │           │
      └───────┬───────┘           │
              │                    │
              ▼                    ▼
      ┌──────────────┐      ┌──────────┐
      │  converge?   │      │arbitrate │
      └──┬───────┬───┘      └────┬─────┘
   未收敛 │       │ 已收敛        │
         ▼       └──────────────┘
   debate(N+1)                    │
                                  ▼
                            ┌──────────┐
                            │  output  │
                            └────┬─────┘
                                 ▼
                            ┌──────────┐
                            │ __end__  │
                            └──────────┘
```

---

## 工作流程

### 完整流程（含辩论）

```
Step 1: 代码输入
   ↓
Step 2: 各 Agent 独立审查 ──────────── 并行执行
   ↓
Step 3: Judge 检测分歧并定位冲突
   ├─ 分析各 Agent 的 findings，标记冲突观点对
   ├─ 无分歧 → 直接进入 Step 5
   └─ 有分歧 → 进入 Step 4
   ↓
Step 4:  靶向辩论阶段
   ├─ 仅冲突 Agent 互看对方那部分观点
   │  其他 Agent 不参与、不知情、不消耗 Token
   │  → 反驳辩护，迭代直至收敛或达上限轮次
   ↓
Step 5: Arbiter 综合所有观点（含辩论结论）
   ↓
Step 6: 输出最终评审报告
```

### 简洁流程（无辩论）

```
代码输入 → 各 Agent 审查 → 无分歧 → 仲裁 → 输出报告
```

---

## 功能特性

### 1. 多智能体辩论机制

- **多维度审查**：5 个预设专业 Agent，每个有独特的系统 Prompt 和评估标准
- **并行执行**：按模型分组，每组第一个 Agent 先执行（KV Cache 预热），其余并行
- **分歧检测**：
  - Judge Agent 自动检测各 Agent 的结构化输出，按 `category` + 行号做观点对齐，标记冲突观点对
- ** 靶向辩论**：当存在分歧时，
  - Judge 定位具体冲突的 Agent 对（如：安全 Agent 和 性能 Agent 在 eval() 上观点冲突）
  - **仅冲突的 Agent 参与辩论**，互看对方的那部分观点，进行反驳与辩护
  - 其他不相关的 Agent 不参与、不知情、不浪费 Token
  - 辩论过程中持续监控是否收敛，已达共识的分歧点不再继续
- **收敛保护**：最大 3 轮辩论，防止无限循环
- **仲裁判决**：Arbiter 综合所有意见（不做 LLM 调用），对争议点做出最终裁定

### 2. 记忆系统

基于 SQLite 持久化的审查历史记忆：

| 记忆类型 | 作用域 | 存储方式 | 说明 |
|---------|--------|---------|------|
| 会话记忆 | 单次运行 | 内存 | 当前对话上下文 |
| 项目记忆 | 单个项目 | SQLite | 同一项目的多次审查历史 |
| 全局记忆 | 所有项目 | SQLite | 跨项目的经验积累 |

**记忆内容：**
- 历史审查记录（代码 + 评审结果）
- 每次审查后自动保存，下次同会话提交时注入到 Agent prompt 中

```python
# 记忆系统可通过配置开关
memory:
  enabled: true
  backend: sqlite
  path: ./data/memory/memory.db
```

### 3. Token 消耗追踪

精确追踪每次运行的 Token 使用量和费用：

- **按 Agent 统计**：每个 Agent 的输入/输出 Token 数
- **按阶段统计**：审查阶段 / 辩论阶段 / 仲裁阶段的消耗
- **费用估算**：基于模型单价自动计算

### 4. 自定义智能体 Prompt

通过 YAML 配置文件完全自定义每个 Agent 的行为：

```yaml
# agents_config.yaml
agents:
  security_expert:
    name: "安全审查专家"
    model: gpt-4o
    temperature: 0.2
    enabled: true
    system_prompt: |
      你是一名资深安全工程师，专门负责代码安全审查。
      ...
    output_schema: "SecurityFinding"

  performance_expert:
    name: "性能优化专家"
    model: gpt-4o-mini
    temperature: 0.1
    enabled: true
    system_prompt: |
      你是一名资深后端性能优化工程师，专门分析代码性能问题。
      ...
    output_schema: "PerformanceFinding"
```

支持：
- 任意数量 Agent（开箱即用 5 个，可扩展）
- 每个 Agent 可指定不同模型（省钱策略：简单 Agent 用便宜模型）
- 运行时通过 Web UI 动态加载/卸载 Agent

### 5. 结构化输出

使用 Pydantic 定义严格的输出 Schema，确保结果可解析、可处理：

```python
class CodeFinding(BaseModel):
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str
    title: str
    description: str
    code_snippet: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    suggestion: Optional[str] = None

class AgentReview(BaseModel):
    agent_name: str
    overall_score: float
    findings: list[CodeFinding]
    summary: str
    confidence: float

class FinalReport(BaseModel):
    summary: str
    overall_score: float
    all_findings: list[FinalReportFinding]
    resolved_disputes: list[dict]
    unresolved_disputes: list[dict]
    recommendations: list[str]
    token_usage: UsageSummary
```

### 6. ⚡ Prompt 缓存系统（KV Cache 共享）

**核心问题：** 所有 Agent 审查同一段代码，代码部分每次都重复传给 LLM——浪费 Token 和计算量。

**关键设计：代码放前面，指令放后面。**

```
┌─ System Prompt ───────────────────────────┐
│  安全审查专家的专业 System Prompt          │  ← 每个 Agent 不同
├─ User Message ────────────────────────────┤
│  Block 0: 代码全文（共享，占 90%+ Token）│  ← 相同！命中 KV Cache
│  Block 1: 分析指令（Agent 特有）          │  ← 每次不同
└───────────────────────────────────────────┘
```

**为什么代码要放前面？**

LLM 的 KV Cache 是按 **token 前缀** 匹配的。同模型 Agent 的 User Message 都以相同代码开头 → 代码部分的 KV Cache 被复用。如果把指令放前面、代码放后面，前缀就完全不同了，缓存无法生效。

#### 执行策略

```
Phase 1 ── 缓存预热（每组第 1 个 Agent 先跑）
  ├─ Agent_安全(gpt-4o, 写缓存)     ← 组间并行
  └─ Agent_性能(gpt-4o-mini, 写缓存) ← 组间并行

Phase 2 ── 并行执行（命中缓存）
  ├─ Agent_正确性(gpt-4o, 命中缓存)  ← 并行
  ├─ Agent_架构(gpt-4o, 命中缓存)    ← 并行
  └─ Agent_风格(gpt-4o-mini, 命中缓存) ← 并行
```

#### 实现方式

**OpenAI / DeepSeek**：User Message 以代码开头，同模型 Agent 自动命中 prefix caching。

**Anthropic**：在代码 content block 上加 `cache_control: ephemeral`。

**本地模型（vLLM/Ollama）**：Automatic Prefix Caching (APC)。

辩论阶段通过缓存复用 `SharedCodeContext`，代码前缀不变，辩论的额外内容追加在指令之前，不破坏缓存。

---

### 7. Diff 感知审查

支持基于 `git diff` 的增量代码审查，仅审查变更的代码行，大幅节省 Token 并聚焦于真正的改动。

**两种使用方式：**

**Web UI：** 点击「Diff 审查」模式切换标签，上传 `.diff` 文件或粘贴 `git diff` 输出。

**CLI：** 三种命令适应不同场景：

```bash
# 方式一：对比两个文件
python main.py diff-review old.py new.py

# 方式二：直接解析 git diff 输出
git diff HEAD~1 > changes.diff
python main.py git-diff changes.diff

# 方式三：管道传 stdin
git diff HEAD~1 | python main.py git-diff

# 批量扫描 + git diff 过滤
python main.py scan ./src/ --git-diff HEAD~1
```

**实现原理：**
- `src/diff/parser.py` 使用正则解析 unified diff 格式，分离出 `+`（新增行）、`-`（删除行）和上下文行
- 送入 LLM 时标记 `+` 行为重点审查目标，`-` 行会被忽略，上下文行仅供参考
- 完整的审查管道（解析 → 并行审查 → 分歧检测 → 辩论 → 仲裁）同样适用于 Diff 模式

---

## 快速开始

### 环境要求

- Python 3.10+
- LLM API Key（OpenAI / Anthropic / DeepSeek / 任意兼容 API）

### 安装

```bash
# 1. 进入项目目录
cd CodeCritic

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

### 启动 Web UI

```bash
uvicorn web.server:app --host 127.0.0.1 --port 8088
```

打开浏览器访问 `http://127.0.0.1:8088`

### CLI 模式

```bash
# 审查一个代码文件
python main.py file myapp.py

# Diff 审查：对比两个文件
python main.py diff-review old.py new.py

# Diff 审查：读取 git diff 文件
python main.py git-diff changes.diff

# 批量扫描目录
python main.py scan ./src/

# 交互模式
python main.py interactive
```

---

## 项目结构

```
CodeCritic/
├── README.md                   # 语言选择页
├── README.zh-CN.md             # 中文文档（本文件）
├── README.en.md                # English Documentation
├── .env.example                # 环境变量模板
│
├── config/
│   ├── settings.yaml           # 全局配置
│   └── agents.yaml             # Agent 定义
│
├── web/
│   ├── server.py               # FastAPI Web 服务器
│   └── static/
│       └── index.html          # 前端页面（GPT 风格 UI）
│
├── src/
│   ├── diff/
│   │   ├── __init__.py
│   │   └── parser.py          # Diff 解析、生成与 LLM 格式化
│   │
│   ├── core/
│   │   └── service.py          # 审查核心服务
│   │
│   ├── graph/
│   │   ├── builder.py          # LangGraph 图构建
│   │   ├── nodes.py            # 各图节点定义
│   │   ├── edges.py            # 条件路由
│   │   └── state.py            # State 定义
│   │
│   ├── agents/
│   │   ├── base.py             # Agent 基类
│   │   ├── security_agent.py
│   │   ├── performance_agent.py
│   │   ├── style_agent.py
│   │   ├── correctness_agent.py
│   │   ├── architecture_agent.py
│   │   └── judge_agent.py      # 分歧检测 Agent
│   │
│   ├── cache/
│   │   └── prompt_cache.py     # Prompt 缓存与模型分组
│   │
│   ├── memory/
│   │   └── base.py             # 记忆系统（SQLite 持久化）
│   │
│   ├── models/
│   │   └── schemas.py          # Pydantic 模型定义
│   │
│   ├── output/
│   │   └── report_service.py   # 报告格式化和持久化
│   │
│   ├── tracking/
│   │   └── token_tracker.py    # Token 用量追踪
│   │
│   └── utils/
│       ├── config_loader.py    # 配置加载
│       ├── logger.py           # 日志配置
│       └── path_utils.py       # 路径安全工具
│
├── main.py                     # CLI 入口
├── view_memory.py              # 记忆数据库查看脚本
├── requirements.txt            # Python 依赖
└── logo.png                    # 项目 Logo
```

---

## 配置指南

### 全局配置 (`config/settings.yaml`)

```yaml
project:
  name: "CodeCritic"
  version: "0.1.0"

llm:
  provider: openai    # openai | anthropic | azure | ollama | custom
  default_model: gpt-4o
  timeout: 60
  max_retries: 3

agents:
  parallel: true
  default_temperature: 0.2

debate:
  enabled: true
  max_rounds: 3

memory:
  enabled: true
  backend: sqlite
  path: ./data/memory/memory.db

token_tracking:
  enabled: true
  log_level: info
```

### 模型配置 (`config/agents.yaml`)

每个 Agent 引用 `models.yaml` 中定义的模型名，实际 API Key 从 `.env` 读取。

---

## 使用示例

### Web UI

打开 `http://127.0.0.1:8088`，粘贴代码，选择 Agent，点击发送。

- ⚙️ 设置弹窗：选择 Agent、模型、开关辩论/记忆
- 💬 对话管理：新建/切换/删除对话
- 🧠 记忆功能：同一对话内第二次审查会参考上一次的结果

### CLI

```bash
# 基本用法：审查文件
python main.py --file myapp.py

# 交互模式
python main.py --interactive
```

---

## 技术栈

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| 框架 | LangChain + LangGraph | Agent 编排与状态图 |
| LLM | OpenAI / Anthropic / DeepSeek / Ollama | 多模型支持 |
| 结构化输出 | Pydantic v2 | Schema 验证 |
| 记忆存储 | SQLite | 持久化方案 |
| 配置 | PyYAML | 灵活配置 |
| Web 后端 | FastAPI | REST API |
| Web 前端 | 原生 HTML/CSS/JS | GPT 风格对话界面 |
| CLI | Typer + Rich | 命令行界面 |

---

## 路线图

### v0.1 — MVP
- [x] 基础 Agent 框架与 5 个预设 Agent
- [x] LangGraph 图构建与状态流转
- [x] 并行审查执行（KV Cache 预热）
- [x] Web UI（GPT 风格对话界面）

### v0.2 — 辩论与记忆
- [x] 分歧检测 (Judge Agent)
- [x] 辩论引擎（多轮交互 + 收敛检测）
- [x] 记忆系统（SQLite 持久化 + 跨会话上下文注入）

### v0.3 — 体验与集成
- [x] Token 追踪与费用估算
- [x] 对话管理（新建/切换/删除）
- [x] 自定义模型配置（Web UI）
- [x] Diff 感知增量审查（CLI + Web UI）
- [x] 批量目录扫描
- [x] 报告导出（Markdown / JSON）

### 未来计划
- [ ] CI/CD 集成（GitHub Action）
- [ ] VS Code 插件
- [ ] PR 自动审查（GitHub App）
- [ ] 基准测试与准确率验证

---

## 许可证

MIT License

---

**CodeCritic** — 让代码审查从一个人的经验，变成一群专家的会诊。⚡
