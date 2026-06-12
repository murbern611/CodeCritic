<p align="center">
  <img src="./logo.png" alt="CodeCritic Logo" width="120">
</p>

<h1 align="center">CodeCritic ⚡ — Multi-Agent Code Review & Debate System</h1>

<p align="center">
  A LangChain + LangGraph multi-agent collaboration framework where specialized agents review code from different dimensions, reach consensus through debate, and produce high-quality review reports.
</p>

---

## Table of Contents

- [Background](#background)
- [Architecture](#architecture)
- [Workflow](#workflow)
- [Features](#features)
  - [Multi-Agent Debate Mechanism](#1-multi-agent-debate-mechanism)
  - [Memory System](#2-memory-system)
  - [Token Usage Tracking](#3-token-usage-tracking)
  - [Custom Agent Prompts](#4-custom-agent-prompts)
  - [Structured Output](#5-structured-output)
  - [Prompt Cache System (KV Cache Sharing)](#6-prompt-cache-systemkv-cache-sharing)
  - [Diff-Aware Code Review](#7-diff-aware-code-review)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [Tech Stack](#tech-stack)
- [Roadmap](#roadmap)

---

## Background

Traditional code review relies on human effort — time-consuming and inconsistent. Most AI code review tools use a **single-agent mode**, where one model handles everything from start to finish with a narrow perspective.

CodeCritic adopts a **multi-agent debate architecture**:

- **Security Expert** — Finds vulnerabilities, injection risks, sensitive data leaks
- **Performance Expert** — Analyzes complexity, bottlenecks, caching opportunities
- **Style Expert** — Checks code conventions, readability, best practices
- **Correctness Expert** — Detects logic errors, edge cases, race conditions
- **Architecture Expert** — Evaluates design patterns, coupling, extensibility

Agents review independently → detect disagreements → **debate when necessary** → arbitrate for final report.

---

## Architecture

```
                    ┌──────────┐
                    │  __start__│
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │  parse   │  ← Parse input code
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │  review  │  ← Parallel agent review (cache warmup)
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │  judge   │  ← Detect disagreements & conflict pairs
                    └────┬─────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
      ┌──────────────┐      ┌──────────┐
      │  debate(N)    │      │  skip    │  ← Conditional routing
      │ only conflic- │      └────┬─────┘
      │ ting agents   │           │
      └───────┬───────┘           │
              │                    │
              ▼                    ▼
      ┌──────────────┐      ┌──────────┐
      │  converge?   │      │arbitrate │
      └──┬───────┬───┘      └────┬─────┘
   no  │       │  yes          │
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

## Workflow

### Full Workflow (with Debate)

```
Step 1: Code Input
   ↓
Step 2: Agent Independent Review ──── Parallel Execution
   ↓
Step 3: Judge detects disagreements
   ├─ Analyzes findings, marks conflicting opinion pairs
   ├─ No conflicts → Step 5
   └─ Conflicts found → Step 4
   ↓
Step 4: Targeted Debate
   ├─ Only conflicting agents exchange relevant opinions
   │  Other agents don't participate, don't waste tokens
   │  → Rebuttal & defense, iterate until convergence or max rounds
   ↓
Step 5: Arbiter synthesizes all perspectives
   ↓
Step 6: Final Report Output
```

### Quick Workflow (No Debate)

```
Code Input → Agent Review → No Conflicts → Arbitrate → Report
```

---

## Features

### 1. Multi-Agent Debate Mechanism

- **Multi-dimensional Review**: 5 preset specialized agents, each with unique system prompts
- **Parallel Execution**: Grouped by model, first agent per group runs first (KV Cache warmup), rest run in parallel
- **Conflict Detection**: Judge Agent automatically detects conflicting opinions by `category` + line number alignment
- **Targeted Debate**: Only conflicting agents exchange their relevant viewpoints for rebuttal and defense
- **Convergence Protection**: Max 3 debate rounds to prevent infinite loops
- **Arbitration Ruling**: Arbiter synthesizes all opinions (no LLM call) for final verdict on disputed points

### 2. Memory System

SQLite-persisted review history:

| Memory Type | Scope | Storage | Description |
|------------|-------|---------|-------------|
| Session Memory | Single run | In-memory | Current conversation context |
| Project Memory | Single project | SQLite | Review history for same project |
| Global Memory | All projects | SQLite | Cross-project experience |

**Memory Contents:**
- Historical review records (code + results)
- Auto-saved after each review, injected into Agent prompts for next submission

### 3. Token Usage Tracking

Precise tracking of token consumption and costs:

- **By Agent**: Input/output tokens per agent
- **By Phase**: Review / Debate / Arbitration phase costs
- **Cost Estimate**: Auto-calculated based on model unit price

### 4. Custom Agent Prompts

Fully customize each Agent's behavior through YAML configuration:

```yaml
# agents_config.yaml
agents:
  security_expert:
    name: "Security Expert"
    model: gpt-4o
    temperature: 0.2
    enabled: true
    system_prompt: |
      You are a senior security engineer specializing in code security review.
      ...
    output_schema: "SecurityFinding"
```

Supports:
- Arbitrary number of agents (5 out-of-the-box, extensible)
- Different models per agent (cost-saving: simple agents use cheap models)
- Dynamic load/unload through Web UI at runtime

### 5. Structured Output

Strict output schemas using Pydantic for parseable, processable results:

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

class FinalReport(BaseModel):
    summary: str
    overall_score: float
    all_findings: list[FinalReportFinding]
    resolved_disputes: list[dict]
    unresolved_disputes: list[dict]
    recommendations: list[str]
    token_usage: UsageSummary
```

### 6. ⚡ Prompt Cache System (KV Cache Sharing)

**Core Problem:** All agents reviewing the same code repeatedly transmit identical code to the LLM — wasting tokens and computation.

**Key Design: Code first, instructions later.**

```
┌─ System Prompt ───────────────────────────┐
│  Security Expert's system prompt          │  ← Different per agent
├─ User Message ────────────────────────────┤
│  Block 0: Full code (90%+ tokens shared)  │  ← Same! Hits KV Cache
│  Block 1: Analysis instructions (unique)  │  ← Different per agent
└───────────────────────────────────────────┘
```

**Why code first?** KV Cache matches on **token prefix**. When same-model agents share the same code prefix, the code's KV Cache is reused.

#### Execution Strategy

```
Phase 1 ── Cache Warmup (first agent per group)
  ├─ Agent_Security(gpt-4o, write cache)   ← Parallel across groups
  └─ Agent_Performance(gpt-4o-mini, write cache)

Phase 2 ── Parallel Execution (cache hit)
  ├─ Agent_Correctness(gpt-4o, cache hit)  ← Parallel
  ├─ Agent_Architecture(gpt-4o, cache hit) ← Parallel
  └─ Agent_Style(gpt-4o-mini, cache hit)   ← Parallel
```

#### Implementation

- **OpenAI / DeepSeek**: Code-first user message, same-model agents auto-hit prefix caching
- **Anthropic**: `cache_control: ephemeral` on code content block
- **Local models (vLLM/Ollama)**: Automatic Prefix Caching (APC)

---

### 7. Diff-Aware Code Review

Review only changed lines using `git diff` — saves tokens and focuses on what matters.

**Web UI:** Switch to "Diff Review" mode tab, upload a `.diff` file or paste `git diff` output.

**CLI:** Three commands for different scenarios:

```bash
# Compare two files
python main.py diff-review old.py new.py

# Parse a git diff file
git diff HEAD~1 > changes.diff
python main.py git-diff changes.diff

# Pipe from stdin
git diff HEAD~1 | python main.py git-diff

# Batch scan with git diff filter
python main.py scan ./src/ --git-diff HEAD~1
```

**How it works:**
- `src/diff/parser.py` parses unified diff format using regex, separating `+` (added), `-` (deleted), and context lines
- LLM receives diff with markers: `+` lines are the review focus, `-` lines are skipped, context is for reference only
- The full review pipeline (parse → parallel review → conflict detection → debate → arbitrate) works identically in diff mode

---

## Quick Start

### Prerequisites

- Python 3.10+
- LLM API Key (OpenAI / Anthropic / DeepSeek / any compatible API)

### Installation

```bash
# 1. Enter project directory
cd CodeCritic

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Start Web UI

```bash
uvicorn web.server:app --host 127.0.0.1 --port 8088
```

Open your browser at `http://127.0.0.1:8088`

### CLI Mode

```bash
# Review a file
python main.py file myapp.py

# Diff review: compare two files
python main.py diff-review old.py new.py

# Diff review: read git diff file
python main.py git-diff changes.diff

# Batch scan directory
python main.py scan ./src/

# Interactive mode
python main.py interactive
```

---

## Project Structure

```
CodeCritic/
├── README.md                   # You are here
├── .env                        # Environment variables (API Key etc.)
│
├── config/
│   ├── settings.yaml           # Global config
│   └── agents.yaml             # Agent definitions
│
├── web/
│   ├── server.py               # FastAPI web server
│   └── static/
│       └── index.html          # Frontend (GPT-style UI)
│
├── src/
│   ├── diff/
│   │   ├── __init__.py
│   │   └── parser.py          # Diff parsing, generation & LLM formatting
│   │
│   ├── graph/
│   │   ├── builder.py          # LangGraph graph builder
│   │   ├── nodes.py            # Graph node functions
│   │   ├── edges.py            # Conditional routing
│   │   └── state.py            # State definition
│   │
│   ├── agents/
│   │   ├── base.py             # Agent base class
│   │   ├── security_agent.py
│   │   ├── performance_agent.py
│   │   ├── style_agent.py
│   │   ├── correctness_agent.py
│   │   ├── architecture_agent.py
│   │   └── judge_agent.py      # Conflict detection agent
│   │
│   ├── core/
│   │   └── service.py          # Core review service
│   │
│   ├── cache/
│   │   └── prompt_cache.py     # Prompt cache & model grouping
│   │
│   ├── memory/
│   │   └── base.py             # Memory system (SQLite)
│   │
│   ├── models/
│   │   └── schemas.py          # Pydantic model definitions
│   │
│   ├── output/
│   │   └── report_service.py   # Report formatting & saving
│   │
│   ├── tracking/
│   │   └── token_tracker.py    # Token usage tracking
│   │
│   └── utils/
│       ├── config_loader.py    # Config loading
│       ├── logger.py           # Logging config
│       └── path_utils.py       # Path security utilities
│
├── main.py                     # CLI entry point
├── view_memory.py              # Memory DB viewer
├── requirements.txt            # Python dependencies
└── logo.png                    # Project Logo
```

---

## Configuration

### Global Config (`config/settings.yaml`)

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

---

## Usage Examples

### Web UI

Open `http://127.0.0.1:8088`, paste code, select agents, click send.

- ⚙️ Settings modal: Select agents, models, toggle debate/memory
- 💬 Conversation management: New/switch/delete conversations
- 🧠 Memory: Second review in same session references first review's findings

### CLI

```bash
# Review a file
python main.py file myapp.py

# Diff review: compare two files
python main.py diff-review old.py new.py

# Diff review: read git diff file
python main.py git-diff changes.diff

# Batch scan directory
python main.py scan ./src/

# Interactive mode
python main.py interactive
```

---

## Tech Stack

| Component | Technology | Description |
|-----------|-----------|-------------|
| Framework | LangChain + LangGraph | Agent orchestration & state graph |
| LLM | OpenAI / Anthropic / DeepSeek / Ollama | Multi-model support |
| Structured Output | Pydantic v2 | Schema validation |
| Memory | SQLite | Persistence |
| Config | PyYAML | Flexible config |
| Web Backend | FastAPI | REST API |
| Web Frontend | Vanilla HTML/CSS/JS | GPT-style chat UI |
| CLI | Typer + Rich | Command line interface |

---

## Roadmap

### v0.1 — MVP
- [x] Base agent framework with 5 preset agents
- [x] LangGraph graph construction & state management
- [x] Parallel review execution (KV Cache warmup)
- [x] Web UI (GPT-style chat interface)

### v0.2 — Debate & Memory
- [x] Conflict detection (Judge Agent)
- [x] Debate engine (multi-round interaction + convergence detection)
- [x] Memory system (SQLite persistence + cross-session context)

### v0.3 — Experience & Integration
- [x] Token tracking & cost estimation
- [x] Conversation management (new/switch/delete)
- [x] Custom model config (Web UI)
- [x] Diff-aware incremental review (CLI + Web UI)
- [x] Batch directory scanning
- [x] Report export (Markdown / JSON)

### Future Plans
- [ ] CI/CD integration (GitHub Action)
- [ ] VS Code extension
- [ ] PR auto-review (GitHub App)
- [ ] Benchmark testing & accuracy validation

---

## License

MIT License

---

**CodeCritic** — Turn code review from one person's experience into a panel of experts. ⚡
