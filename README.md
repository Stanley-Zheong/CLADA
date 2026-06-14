# CLADA

**C**losed-**L**oop **A**utonomous **D**evelopment **A**rchitecture

A governance framework that wraps AI coding agents (like Claude Code) in a verifiable development pipeline — giving the human Owner control through a machine-readable constitution, a formal state machine, and (planned) physical isolation of the agent process.

> **Status at a glance.** The machine-readable constitution, the state machine, the contract/DR validators, and the DSL compiler are **implemented and tested** today. Physical isolation (process suspension, read-only locking, file-write monitoring) is **best-effort or planned** — see the [Maturity note](#cli-commands) and the Implementation Phases table below. README claims describe the target design, not all currently-shipping behavior.

## Design Philosophy

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
│       ├── orchestrator.py    # State machine + PTY manager + Gateway REPL
│       ├── bootstrap.py       # Bootstrap flow + Memory Manager
│       ├── contract_validator.py  # Contract/DR validation + L2 index
│       ├── config.py          # LLM role configuration (.clada/config.yml)
│       └── dsl/               # S-expression DSL → contract.json + spec.md
├── tests/                     # Baseline pytest suite (state machine, validators, DSL)
├── docs/
│   └── CLADA_Complete_Spec.html  # Full technical specification
├── pyproject.toml             # Packaging + console script (clada)
├── requirements.txt           # Flat dependency mirror of pyproject.toml
├── .gitignore
└── README.md
```

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

### First Run

```bash
clada help                        # list all commands
clada dsl domains                 # list built-in DSL domains (no project needed)
```

### Bootstrap a New Project

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

> **Maturity note (Phase 1).** Installable packaging, the state machine, the
> contract/DR validators, and the DSL compiler are implemented and covered by
> the `tests/` suite. The live Gateway loop (`clada` with no args), PTY
> suspension, `fswatch` monitoring, and `clada run` are best-effort or planned —
> see the Implementation Phases and Technical Risk Register below.

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

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | PTY wrapping, State Machine, Contract Validator, Bootstrap | In progress |
| **Phase 2** | Docker test isolation, chmod locks, fswatch, Heartbeat, L2 index | Planned |
| **Phase 3** | Dual-Lock Bootstrap UI, L3 vector DB, Clean Shutdown, Owner console | Planned |

## Technical Risk Register

Key assumptions requiring empirical verification:

- **RISK-01**: SIGSTOP beyond ~60s may cause TCP timeout with Anthropic API → re-inject context on resume
- **RISK-02**: fswatch capture rate on bind mounts → chmod 555 as primary defense
- **RISK-03**: Heartbeat probe may trigger unintended Agent response → PTY-level filtering
- **RISK-04**: LD_PRELOAD file interception unavailable on macOS SIP → chmod-based alternative

## License

MIT
