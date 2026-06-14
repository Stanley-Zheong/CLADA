# CLADA

**C**losed-**L**oop **A**utonomous **D**evelopment **A**rchitecture

A **runtime safety harness for AI coding agents**. CLADA wraps any agent process (`claude`, `codex`, or your own) with `clada run`: it hardens its own guardrail files, monitors for unauthorised writes, logs every session as structured JSONL, and produces an audit report with a path-scoped rollback plan — so an autonomous agent cannot silently rewrite the files that constrain it, and every run is reviewable and reversible.

> **Start here — the wedge that ships today:**
> ```bash
> pip install -e .
> clada run -- echo ok                 # supervise any command
> bash examples/protected_write_demo.sh  # full offline demo (no LLM, no API cost)
> ```
> See the annotated walkthrough in [`docs/demo.md`](docs/demo.md).

> **Status at a glance.** The `clada run` session supervisor, the policy engine
> with fail-closed degraded enforcement, the redacted JSONL event log, and the
> audit report with safe path-scoped rollback are **implemented and tested**
> today (Phases 1–5). The broader governance vision below — the interactive
> Gateway state machine, Dual-Lock contract generation, process suspension, and
> container isolation — is the **target design and is planned/best-effort**, not
> all currently-shipping behavior. Each claim is marked. See the Implementation
> Phases table and [`docs/known-limitations.md`](docs/known-limitations.md).

## Runtime Safety Harness (shipping today)

This is the part of CLADA that is implemented and covered by `tests/`. Run any
agent under `clada run` and you get four guarantees on every session:

| Guarantee | How |
|-----------|-----|
| **Guardrails are protected** | CLADA `chmod`-hardens its own decision/runtime directories to read-only and (when `fswatch` is present) monitors them; the agent cannot rewrite the files that constrain it. *Best-effort speed-bump, not a sandbox — see [known limitations](docs/known-limitations.md).* |
| **Honest degraded mode** | If hardening or monitoring is unavailable, CLADA records a structured *degraded* status and **fails closed** (exit `78`) rather than pretending to be protected. |
| **Every session is logged** | One redacted JSONL event per line under `runtime/sessions/` — secrets are omitted before write. Schema: [`docs/jsonl-event-schema.md`](docs/jsonl-event-schema.md). |
| **Safe, scoped rollback** | An audit report under `runtime/audits/` separates session-owned changes from pre-existing work and emits rollback commands targeting only what the session touched. |

```bash
clada run -- claude        # or: clada run -- codex, or any command
```

Try it offline, with no LLM and no API cost:

```bash
bash examples/protected_write_demo.sh
```

The demo runs a shell "agent" that attempts a protected write, shows it being
blocked, and prints the session log, audit report, and rollback plan. Full
walkthrough: [`docs/demo.md`](docs/demo.md) · examples: [`examples/`](examples/).

---

## Design Philosophy

> The sections from here on describe the **broader governance design** CLADA is
> growing toward. Much of it (the interactive Gateway, Dual-Lock generation,
> process suspension, container isolation) is **planned or best-effort**, not yet
> shipping — each item is marked in the Key Features and Implementation Phases
> tables. The runtime harness above is the part that works today.

AI coding agents are powerful but unbounded. They hallucinate, drift from specs, and resist rollback after hundreds of iterations. CLADA imposes **constitutional constraints** on autonomous development through three interlocking mechanisms:

| Mechanism | Role |
|-----------|------|
| **Contract** (contract.json) | Machine-readable constitution — defines what the system MUST do, MUST NOT do, and how to verify |
| **Gateway** (State Machine) | Runtime controller — enforces the 8-state lifecycle and gates every transition |
| **Verifier** (Audit + Dual-Lock) | Independent validator — audits every Executor output before merge |

## Architecture

```
┌─────────────────────────────────────────┐
│                  Owner                   │
│            (Slash Commands)              │
└──────────┬──────────────────────────────┘
           │ /init, /propose, /execute, /merge, /abort
           ▼
┌─────────────────────────────────────────┐
│               Gateway                    │
│  ┌───────────────────────────────────┐  │
│  │  State Machine (8 states)         │  │
│  │  PTY Manager · Pattern Monitor    │  │
│  │  Heartbeat · File Access Proxy    │  │
│  └───────────────────────────────────┘  │
└──────┬────────────────────┬─────────────┘
       │                    │
       ▼                    ▼
┌──────────────┐   ┌──────────────┐
│   Executor   │   │   Verifier   │
│  (AI Agent)  │   │  (Validator) │
│              │   │              │
│  Write src/  │   │  Read-only   │
│  Cannot      │   │  Audit +     │
│  write docs/ │   │  Arbitrate   │
└──────────────┘   └──────────────┘
```

### Three-Role Separation of Powers

| Permission | Owner | Executor (AI) | Verifier (AI) |
|------------|-------|---------------|---------------|
| Write source code | Yes | Yes | No |
| Write docs / contract | Yes | No (Gateway blocks) | Yes |
| Read source code | Yes | Yes (read-only) | Yes |
| Trigger state transitions | Yes (`/slash` commands) | Partial (output triggers) | Partial (audit conclusions) |

### State Machine

```
IDLE ──/init──▶ BOOTSTRAP ──confirm──▶ IDLE
  │                                       
  ├──/propose──▶ PROPOSING ──spec ready──▶ EXECUTING
  │                                           │  ▲
  │                      ┌────────────────────┤  │
  │                      │  [REQ_REVIEW]      │  │
  │                      ▼                    │  │
  │                  SUSPENDED ──verdict──▶ ARBITRATING
  │                                           │
  │                      [DONE]               │
  │                        ▼                  │
  │                    AUDITING ──fail──▶ EXECUTING
  │                        │                  
  │                   ┌────┴────┐            
  │              pass+bplan   pass clean      
  │                   │          │            
  │                   ▼          ▼            
  │          WAITING_FOR_OWNER  PENDING_COMMIT
  │                                 │         
  ◀───────────── /merge ───────────┘         
```

## Key Features

The list below describes the **target design**. The Status column marks what ships
today versus what is best-effort or planned (see Implementation Phases for detail).

| Feature | Description | Status |
|---------|-------------|--------|
| **ADR-Based Decision Records** | Every architectural decision tracked as machine-readable frontmatter with formal validation | Implemented |
| **Three-Tier Memory** | L1 (immediate), L2 (structural DR index), L3 (historical archives) — designed to combat hallucination beyond 100 iterations | L2 index implemented; L3 planned |
| **Pattern Monitor** | Regex-triggered state transitions (`[REQ_REVIEW]`, `[DONE]`, `[B_PLAN]`, `[TRACE]`) | Best-effort (needs a live Gateway) |
| **Physical Isolation** | `SIGSTOP`/`SIGCONT` for Executor suspension; `chmod 555` read-only locking during audit | Planned (Phase 2) — not yet enforced |
| **Dual-Lock Contract Generation** | Two independent AI models generate project constitutions; Gateway performs field-level diff; Owner only arbitrates conflicts | Planned (Phase 3) |
| **Clean Shutdown Protocol** | Git stash + recovery prompt on quota exhaustion or abnormal termination | Planned (Phase 3) |

## Project Structure

```
CLADA/
├── src/
│   └── clada/                 # Python package
│       ├── __init__.py        # Package exports
│       ├── __main__.py        # CLI entry point (python -m clada / clada)
│       ├── session.py         # clada run Session Supervisor + JSONL log (Phase 2)
│       ├── policy.py          # Path policy + degraded enforcement + redaction (Phase 3)
│       ├── audit.py           # Session audit report generation (Phase 4)
│       ├── checkpoint.py      # Git checkpoint + path-scoped rollback (Phase 4)
│       ├── orchestrator.py    # State machine + PTY manager + FileAccessProxy
│       ├── bootstrap.py       # Bootstrap flow + Memory Manager
│       ├── contract_validator.py  # Contract/DR validation + L2 index
│       ├── config.py          # LLM role configuration (.clada/config.yml)
│       └── dsl/               # S-expression DSL → contract.json + spec.md
├── examples/                  # Offline runtime-harness demo (no LLM)
│   ├── protected_write_demo.sh
│   └── agent_sim.sh
├── tests/                     # pytest suite (supervisor, policy, audit, checkpoint, validators, DSL)
├── docs/
│   ├── demo.md                # Demo walkthrough: output, log, audit, rollback
│   ├── jsonl-event-schema.md  # Session JSONL event schema + examples
│   ├── known-limitations.md   # macOS chmod/fswatch semantics + deferred scope
│   └── CLADA_Complete_Spec.html  # Full technical specification
├── pyproject.toml             # Packaging + console script (clada)
├── requirements.txt           # Flat dependency mirror of pyproject.toml
├── .gitignore
└── README.md
```

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/demo.md`](docs/demo.md) | End-to-end demo: expected output, session log, audit report, rollback path. |
| [`docs/jsonl-event-schema.md`](docs/jsonl-event-schema.md) | Field-by-field schema for every session JSONL event, with examples and exit codes. |
| [`docs/known-limitations.md`](docs/known-limitations.md) | What `chmod`/`fswatch` protection does and does not give you, fail-closed behavior, and explicitly deferred scope. |
| [`examples/README.md`](examples/README.md) | How to run the offline demo and swap in a real agent. |

## Quick Start

### Install (macOS / Linux, Python ≥ 3.9)

```bash
git clone https://github.com/Stanley-Zheong/CLADA.git
cd CLADA
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                  # installs CLADA + dependencies, adds the `clada` command
clada help                        # verify the install
```

Optional extras:

```bash
pip install -e ".[test]"          # alias for the base install — pytest is already included
brew install fswatch              # optional, for file write monitoring (best-effort / planned)
npm install -g @anthropic-ai/claude-code  # Executor agent (required only to run a live Gateway)
```

> `pip install -e .` reads `pyproject.toml`; `requirements.txt` mirrors the same
> dependency set for environments that prefer `pip install -r requirements.txt`.

### Running the Tests

`pytest` is part of the base install, so the baseline suite runs immediately
after `pip install -e .`:

```bash
pytest                            # runs the baseline suite under tests/
```

### First Run — supervise an agent with `clada run`

The primary entry point. `clada run -- <command>` wraps any process in the
runtime safety harness (policy gate → session log → audit report → rollback):

```bash
clada run -- echo ok              # smoke test: supervise a trivial command
clada run -- claude               # supervise a real agent (needs the Claude Code CLI)
clada run -- codex                # …or any command on your PATH
```

Then inspect what the session produced:

```bash
ls runtime/sessions/              # <session-id>.jsonl — structured event log
ls runtime/audits/                # <session-id>.md   — audit report + rollback plan
```

Prefer to see it end to end without an LLM? Run the offline demo:

```bash
bash examples/protected_write_demo.sh   # see docs/demo.md for the walkthrough
```

> If `clada run` exits `78`, enforcement was degraded and CLADA failed closed
> (commonly: `fswatch` not installed). Install `fswatch` and retry, or see
> [`docs/known-limitations.md`](docs/known-limitations.md).

### Other commands (no project needed)

```bash
clada help                        # list all commands
clada dsl domains                 # list built-in DSL domains
```

### Bootstrap a New Project (planned governance flow)

> The interactive Gateway and Bootstrap flow are **planned/best-effort** — not
> part of the shipping runtime harness. See the Implementation Phases table.

```bash
cd your-project
clada init                        # Bootstrap: define Goal + Contract  (alias: python3 -m clada init)
clada                             # Start Gateway                       (alias: python3 -m clada)
```

### Gateway Commands

```
clada> /init              Start Bootstrap (create first Contract + DR-001)
clada> /propose [text]    Enter PROPOSING: Verifier refines Spec
clada> /execute           Start Executor on current_spec.md
clada> /merge             Merge feature branch (PENDING_COMMIT only)
clada> /reject [reason]   Reject audit, return to EXECUTING
clada> /abort             Clean Shutdown and exit
clada> /status            Show current state
clada> /quota [n]         Set ask_verifier quota (default: 10)
clada> /autopilot [on|off] Toggle Owner-offline mode
```

### CLI Commands

After `pip install -e .` these are available both as `clada <cmd>` and `python3 -m clada <cmd>`.

```bash
clada run -- <command>    # Supervise a command (session log + audit + rollback)
clada status              # Show system state
clada validate contract   # Validate docs/spec/contract.json
clada validate dr <file>  # Validate a DR-xxx.md file
clada validate all        # Validate all DRs
clada index rebuild       # Rebuild L2 index.json
clada cold-start          # Scan repo → architecture.md
clada dsl domains         # List available DSL domains
clada dsl compile <file>  # Compile a .dsl file → contract.json + spec.md
clada dsl template <dom>  # Print a DSL template for a domain
clada config init         # Create a default .clada/config.yml
```

> **Maturity note.** Implemented and covered by the `tests/` suite: the
> `clada run` session supervisor (Phase 2), the policy engine with fail-closed
> degraded enforcement and redaction (Phase 3), the audit report with safe
> path-scoped rollback (Phase 4), installable packaging, the state machine, the
> contract/DR validators, and the DSL compiler (Phase 1). Best-effort or
> planned: the live interactive Gateway loop (`clada` with no args), PTY
> suspension, and container isolation — see the Implementation Phases table,
> the Technical Risk Register, and [`docs/known-limitations.md`](docs/known-limitations.md).
> `fswatch` monitoring works when `fswatch` is installed; without it, `clada run`
> fails closed rather than running unprotected.

> **Policy & degraded enforcement (Phase 3).** Path protection is an explicit,
> testable policy (`clada.policy`): which paths are secret-bearing, which the
> Executor may not write, and which it may. Protection relies on best-effort
> `chmod` + `fswatch` and is **not** a sandbox. When either mechanism fails or
> is unavailable, CLADA records a structured *degraded* status, emits
> `policy_degraded` / `policy_violation` session events, and **fails closed** —
> refusing to launch the Executor unless the Owner explicitly approves running
> with reduced protection. Sensitive paths and secret-like strings are redacted
> (omission-preferred) before anything is logged.

## Implementation Phases

The project is delivered as a runtime-safety-harness wedge first, with the
broader governance UI layered on later.

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Installable packaging, State Machine, Contract/DR Validators, DSL compiler, baseline tests | Done |
| **Phase 2** | `clada run` Session Supervisor + redacted JSONL event log | Done |
| **Phase 3** | Policy engine, honest degraded enforcement, fail-closed gate, redaction | Done |
| **Phase 4** | Audit report + safe, path-scoped session rollback | Done |
| **Phase 5** | Reproducible demo, docs aligned to implemented behavior | Done |
| **Later** | Live interactive Gateway, Dual-Lock Bootstrap, PTY suspension, container isolation, L3 memory | Planned / best-effort |

## Technical Risk Register

Key assumptions requiring empirical verification:

- **RISK-01**: SIGSTOP beyond ~60s may cause TCP timeout with Anthropic API → re-inject context on resume
- **RISK-02**: fswatch capture rate on bind mounts → chmod 555 as primary defense
- **RISK-03**: Heartbeat probe may trigger unintended Agent response → PTY-level filtering
- **RISK-04**: LD_PRELOAD file interception unavailable on macOS SIP → chmod-based alternative

## License

MIT
