# AXIOM

> **Status — Legacy research prototype.** AXIOM is preserved as an earlier capability/skill-composition experiment. Active least-authority work has moved to [KAVI Capability Compiler](https://github.com/kOs-tile/kavi-capability-compiler). This repository is not presented as a production security boundary.


[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![DeepSeek V3](https://img.shields.io/badge/Synthesizer-DeepSeek--V3-6B35FF?logo=openai&logoColor=white)](https://deepseek.com)
[![Hermes Compatible](https://img.shields.io/badge/Hermes-compatible-FF6B35)](https://github.com/onurkavi)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Living skill marketplace and autonomous skill synthesizer for the Hermes / Kavi Claw agent framework.**

---

## The Static Tool Problem

Every agent framework ships with a fixed toolbox. When a task doesn't fit any
existing tool, you're stuck — the agent either fails silently or halts asking for
help. Hermes agents are smarter than that, but they still depend on you to write
every skill by hand.

**AXIOM solves this.** It gives Hermes a self-expanding capability layer:

- Already have a skill for the task? → AXIOM finds and ranks it in milliseconds.
- Close but not quite? → AXIOM chains existing skills into a novel composition.
- No match at all? → AXIOM synthesizes a new skill with DeepSeek-V3, runs it through
  a security sandbox, and promotes it to the live registry — all without human intervention.

Skills accumulate. Skills are monitored. Skills that degrade are flagged and replaced.
The registry grows smarter every time Hermes asks it a question.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AXIOM Service (FastAPI)                            │
│                                                                             │
│   ┌──────────────┐   ┌──────────────────────┐   ┌───────────────────────┐  │
│   │  Python SDK  │   │   REST API / WS      │   │   Decay Monitor       │  │
│   │  AxiomClient │   │   /api/v1/*          │   │   (APScheduler)       │  │
│   │  Hermes      │   │   /ws/synthesize      │   │   flag → deprecate    │  │
│   │  Adapter     │   │   /ws/monitor        │   │   → re-synthesize     │  │
│   └──────┬───────┘   └──────────┬───────────┘   └───────────────────────┘  │
│          │                      │                                           │
│   ┌──────▼──────────────────────▼──────────────────────────────────────┐   │
│   │                      Capability Resolver                           │   │
│   │  embed(task) → pgvector search → re-rank by:                       │   │
│   │  score = semantic_sim×0.6 + success_rate×0.3 + recency×0.1         │   │
│   └──────────────────────────┬─────────────────────────────────────────┘   │
│                               │ no match                                    │
│   ┌───────────────────────────▼──────────────────────────────────────────┐  │
│   │                   Skill Composition Engine                           │  │
│   │  NetworkX directed graph — output_schema[i] → input_schema[i+1]     │  │
│   │  DFS path search → chains ranked by joint success_rate              │  │
│   └───────────────────────────┬──────────────────────────────────────────┘  │
│                               │ no valid chain                              │
│   ┌───────────────────────────▼──────────────────────────────────────────┐  │
│   │                      Skill Synthesizer (DeepSeek-V3)                 │  │
│   │                                                                      │  │
│   │  Step 1 ──► Analyze task requirements (structured output)            │  │
│   │  Step 2 ──► Retrieve 3 similar skills as few-shot examples           │  │
│   │  Step 3 ──► Deduplication check (cosine similarity threshold 0.92)   │  │
│   │  Step 4 ──► Generate typed I/O schema                                │  │
│   │  Step 5 ──► Generate Python implementation                           │  │
│   │  Step 6 ──► Generate unit test cases                                 │  │
│   │  Step 7 ──► Sandbox: AST check + bandit scan + RestrictedPython exec │  │
│   │  Step 8 ──► Promote to ACTIVE registry if pass rate ≥ 80%           │  │
│   │                                                                      │  │
│   │  ◄── streams progress via WebSocket (/ws/synthesize) ──────────────  │  │
│   └───────────────────────────┬──────────────────────────────────────────┘  │
│                               │                                             │
│   ┌───────────────────────────▼──────────────────────────────────────────┐  │
│   │                    Skill Registry                                    │  │
│   │  Supabase PostgreSQL + pgvector (1536-dim embeddings)                │  │
│   │  CRUD · semantic search · performance metrics · lifecycle status     │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## How It Works

```
User task description
        │
        ▼
┌───────────────────┐
│  Capability       │  Embed task → pgvector search → re-rank (confidence score)
│  Resolver         │──────────────────────────────────────────────────────────►  Return skill if found
└───────────────────┘
        │ (no match)
        ▼
┌───────────────────┐
│  Composition      │  Build I/O graph → DFS path search → rank chains
│  Engine           │──────────────────────────────────────────────────────────►  Return chain if found
└───────────────────┘
        │ (no valid chain)
        ▼
┌───────────────────┐
│  Synthesizer      │  Analyze → Few-shot examples → Dedup → Schema → Code → Tests
│  (DeepSeek-V3)    │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Sandbox          │  AST permission check → bandit scan → RestrictedPython exec
│  Evaluator        │  Memory cap 256MB · Timeout 30s · Blocked: os/subprocess/socket
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Registry         │  Promote to ACTIVE if pass rate ≥ 80%
│  Promotion        │  Skills are now searchable and chainable
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Decay Monitor    │  Hourly: flag degraded skills · deprecate idle skills
│  (background)     │  auto-queue for re-synthesis
└───────────────────┘
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/kOs-tile/axiom
cd axiom
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY, DEEPSEEK_API_KEY, SUPABASE_URL/keys
```

### 2. Start with Docker Compose

```bash
docker compose up --build
```

This starts:
- **PostgreSQL 15 + pgvector** on port 5432 (with init.sql schema applied)
- **Redis 7** on port 6379
- **AXIOM** on port 8000

### 3. Seed the registry

```bash
python scripts/seed_registry.py
```

Populates the registry with 10 example skills across trading, analysis, formatting, and utility categories.

### 4. Run the interactive demo

```bash
python scripts/demo.py --axiom-url http://localhost:8000
```

The demo:
1. Registers 5 sample skills
2. Resolves an existing skill for a trading task
3. Finds a composition chain for a multi-step task
4. Synthesizes a brand new Bollinger Bands skill with live WebSocket streaming

### 5. Explore the API

```
http://localhost:8000/docs
```

---

## Python SDK — Hermes Drop-in Integration

### Basic Usage

```python
from axiom.sdk.client import AxiomClient

axiom = AxiomClient(base_url="http://localhost:8000")

# Resolve: find best existing skill for a task
skills = await axiom.resolve("compute RSI for a price series", top_k=3)
for r in skills:
    print(f"[{r.rank}] {r.skill.name}  confidence={r.confidence:.3f}")

# Compose: find a multi-step skill chain
chains = await axiom.compose("fetch BTC price then compute Bollinger Bands")
for chain in chains:
    print(" → ".join(s.name for s in chain.skills))

# Synthesize: create a brand new skill (REST, blocking)
result = await axiom.synthesize("normalize a list of prices using min-max scaling")
if result.status == "success":
    print(f"New skill: {result.skill.name} (status={result.skill.status})")

# Invoke: run a registered skill with input data
response = await axiom.invoke_skill(
    skill_id="your-skill-uuid",
    input_data={"prices": [100.0, 102.5, 99.8, 103.1], "period": 14},
)
print(response.output)
```

### Streaming Synthesis (WebSocket)

```python
async for event in axiom.synthesize_stream(
    "compute weighted moving average with exponential decay"
):
    print(f"[{event.progress_pct:3d}%] {event.step.value}: {event.message}")
    if event.step.value == "complete":
        result = event.detail["result"]
        print(f"Skill '{result['skill']['name']}' is now ACTIVE")
        break
```

### Hermes Adapter (drop-in replacement)

```python
from axiom.sdk.hermes_adapter import HermesSkillAdapter

# Create adapter pointing to AXIOM and your local skill directory
adapter = HermesSkillAdapter(
    axiom_url="http://localhost:8000",
    skill_dir="./skills",
    auto_ingest=True,   # auto-register new local skills into AXIOM
)

# Use as Hermes skill loader
hermes.skill_loader = adapter.load

# Or call directly
skill = await adapter.load("compute Sharpe ratio for a portfolio")
# 1. Checks AXIOM registry first
# 2. Falls back to local ./skills/*.py files
# 3. Auto-ingests local skills into AXIOM
# 4. Synthesizes a new skill if nothing is found

# Bulk-ingest all local skills
ingested = await adapter.ingest_directory()
print(f"Ingested {len(ingested)} local skills into AXIOM")
```

### Hermes-compatible `load_skill()` interface

```python
# Single convenience method — resolves or synthesizes, returns a Skill or None
skill = await axiom.load_skill("fetch OHLCV bars from Binance")
if skill:
    result = await axiom.invoke_skill(skill.id, {"symbol": "BTCUSDT", "interval": "1h"})
```

---

## Skill Schema Reference

See **[SKILL_SCHEMA.md](SKILL_SCHEMA.md)** for the full specification.

### Quick example

```json
{
  "name": "compute_rsi",
  "description": "Computes the Relative Strength Index for a list of closing prices.",
  "tags": ["trading", "rsi", "technical_analysis"],
  "category": "analysis",
  "input_schema": {
    "fields": [
      {"name": "prices", "type": "list[float]", "required": true},
      {"name": "period", "type": "int",         "required": false, "default": 14}
    ]
  },
  "output_schema": {
    "fields": [
      {"name": "rsi",        "type": "float"},
      {"name": "overbought", "type": "bool"},
      {"name": "oversold",   "type": "bool"}
    ]
  },
  "implementation": "def run(prices, period=14): ..."
}
```

---

## Security

AXIOM applies three independent security layers to every synthesized skill before promotion:

### Layer 1 — AST Permission Checker

Walks the Python AST of generated code looking for:

| Blocked | Reason |
|---|---|
| `import os`, `import subprocess` | OS/process access |
| `import socket`, `import urllib`, `import requests` | Network access |
| `import pathlib`, `import shutil` | File system access |
| `eval()`, `exec()`, `compile()` | Dynamic code execution |
| `open()` | File I/O |
| `__import__()` | Dynamic import bypass |
| `__class__`, `__subclasses__`, `f_globals` | Sandbox escape via introspection |

Allowed imports: `math`, `statistics`, `json`, `datetime`, `re`, `collections`, `numpy`, `pandas`, `scipy`, and other pure computation libraries.

### Layer 2 — Bandit Static Analysis

Runs [bandit](https://bandit.readthedocs.io) with `--level LOW --confidence LOW` (report everything). Any `HIGH` or `CRITICAL` severity finding blocks promotion.

### Layer 3 — RestrictedPython Execution

Compiled with `RestrictedPython.compile_restricted()` which:
- Transforms the AST to inject access guards (`_getattr_`, `_getitem_`, `_getiter_`)
- Replaces all `__builtins__` with an explicit allowlist
- Blocks `eval`, `exec`, `open`, `__import__`, and `input`
- Enforces a 256 MB memory cap (via `resource.setrlimit`)
- Enforces a 30-second execution timeout (via `asyncio.wait_for`)

### Permission Model

```
Skill code runs with:
  ✓ Pure computation (math, statistics, json, datetime, re, collections)
  ✓ Read-only numpy/pandas operations
  ✗ File system (read or write)
  ✗ Network access (sockets, HTTP, DNS)
  ✗ Subprocess or shell execution
  ✗ Dynamic code execution (eval, exec, compile)
  ✗ Module introspection (__import__, importlib)
  ✗ Sandbox escape via attribute access (__class__, f_globals)
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/resolve` | Embed task → ranked existing skills |
| `POST` | `/api/v1/compose` | Task → skill composition chain |
| `POST` | `/api/v1/synthesize` | Synthesize a new skill (REST) |
| `GET`  | `/api/v1/skills` | List all skills |
| `POST` | `/api/v1/skills` | Register a skill manually |
| `GET`  | `/api/v1/skills/{id}` | Get skill by ID |
| `POST` | `/api/v1/skills/{id}/invoke` | Invoke a skill with input data |
| `GET`  | `/api/v1/skills/{id}/metrics` | Get performance metrics |
| `DELETE` | `/api/v1/skills/{id}` | Delete a skill |
| `GET`  | `/api/v1/monitor/status` | Decay monitor status |
| `POST` | `/api/v1/monitor/run` | Trigger manual decay check |
| `WS`   | `/ws/synthesize` | Streaming synthesis (WebSocket) |
| `WS`   | `/ws/monitor` | Live decay events (WebSocket) |

---

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v --asyncio-mode=auto
```

Test coverage:

| Test file | What it tests |
|---|---|
| `tests/test_composition_engine.py` | Graph construction, DFS path search, I/O compatibility |
| `tests/test_synthesizer.py` | Prompt construction, output parsing, step sequencing, dedup |
| `tests/test_sandbox.py` | RestrictedPython execution, security boundaries, full pipeline |
| `tests/test_resolver.py` | Composite scoring, recency decay, threshold filtering |

---

## Repository Structure

```
axiom/
├── axiom/
│   ├── app.py                   ← FastAPI application entry point
│   ├── config.py                ← Pydantic Settings (all config via env vars)
│   ├── models.py                ← Core Pydantic v2 models
│   ├── api/
│   │   ├── routes.py            ← REST endpoints
│   │   └── websocket.py         ← WebSocket synthesis streaming
│   ├── registry/
│   │   ├── skill_store.py       ← Supabase CRUD + pgvector search
│   │   └── embedder.py          ← OpenAI embedding + cosine similarity
│   ├── resolver/
│   │   ├── capability_resolver.py  ← Composite ranking resolver
│   │   └── composition_engine.py   ← NetworkX I/O graph + chain search
│   ├── synthesis/
│   │   ├── synthesizer.py       ← Multi-step DeepSeek-V3 synthesis agent
│   │   └── deduplication.py     ← Cosine similarity duplicate detection
│   ├── sandbox/
│   │   ├── evaluator.py         ← RestrictedPython sandbox + test runner
│   │   ├── security_scanner.py  ← Bandit subprocess integration
│   │   └── permission_checker.py← AST-based import/call analysis
│   ├── monitor/
│   │   └── decay_monitor.py     ← APScheduler performance decay monitor
│   └── sdk/
│       ├── client.py            ← AxiomClient Python SDK
│       └── hermes_adapter.py    ← Hermes skill loader drop-in
├── scripts/
│   ├── demo.py                  ← Interactive end-to-end demo
│   └── seed_registry.py         ← Populate registry with example skills
├── tests/
│   ├── test_composition_engine.py
│   ├── test_synthesizer.py
│   ├── test_sandbox.py
│   └── test_resolver.py
├── migrations/
│   └── init.sql                 ← PostgreSQL schema + pgvector setup
├── SKILL_SCHEMA.md              ← Skill format documentation
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Configuration

All configuration is via environment variables. See [`.env.example`](.env.example) for the full list.

Key settings:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for embeddings |
| `DEEPSEEK_API_KEY` | — | Required for synthesis |
| `SUPABASE_URL` | — | Supabase project URL |
| `RESOLVER_SEMANTIC_WEIGHT` | `0.6` | Weight for semantic similarity in ranking |
| `DEDUP_COSINE_THRESHOLD` | `0.92` | Similarity above which synthesis is skipped |
| `SANDBOX_MEMORY_LIMIT_MB` | `256` | Max memory per sandbox run |
| `SANDBOX_TIMEOUT_SECONDS` | `30` | Max execution time per sandbox run |
| `PROMOTION_MIN_SUCCESS_RATE` | `0.8` | Minimum test pass rate for promotion |
| `DECAY_SUCCESS_THRESHOLD` | `0.70` | Success rate below which skill is flagged |
| `DECAY_IDLE_DAYS` | `30` | Days of inactivity before deprecation |

---

## License

MIT © [Onur Kavi](https://github.com/onurkavi)
