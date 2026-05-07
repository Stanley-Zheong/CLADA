# CLADA

**C**losed-**L**oop **A**utonomous **D**evelopment **A**rchitecture

A governance framework that wraps AI coding agents (like Claude Code) in a verifiable development pipeline — giving the human Owner control through a machine-readable constitution, a formal state machine, and physical isolation.

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

- **Dual-Lock Contract Generation**: Two independent AI models generate project constitutions; Gateway performs field-level diff; Owner only arbitrates conflicts
- **Physical Isolation**: `SIGSTOP`/`SIGCONT` for Executor suspension; `chmod 555` read-only locking during audit
- **Pattern Monitor**: Regex-triggered state transitions (`[REQ_REVIEW]`, `[DONE]`, `[B_PLAN]`, `[TRACE]`)
- **Three-Tier Memory**: L1 (immediate), L2 (structural DR index), L3 (historical archives) — designed to combat hallucination beyond 100 iterations
- **Clean Shutdown Protocol**: Git stash + recovery prompt on quota exhaustion or abnormal termination
- **ADR-Based Decision Records**: Every architectural decision tracked as machine-readable frontmatter with formal validation

## Project Structure

```
CLADA/
├── src/
│   └── clada/                 # Python package
│       ├── __init__.py        # Package exports
│       ├── __main__.py        # CLI entry point (python -m clada)
│       ├── orchestrator.py    # State machine + PTY manager + Gateway REPL
│       ├── bootstrap.py       # Bootstrap flow + Memory Manager
│       └── contract_validator.py  # Contract/DR validation + L2 index
├── docs/
│   └── CLADA_Complete_Spec.html  # Full technical specification
├── requirements.txt
├── .gitignore
└── README.md
```

## Quick Start

### Prerequisites (macOS)

```bash
pip install rich psutil jsonschema pexpect watchdog
brew install fswatch              # optional, for file write monitoring
npm install -g @anthropic-ai/claude-code  # Executor agent
```

### Bootstrap a New Project

```bash
cd your-project
python3 -m clada init             # Bootstrap: define Goal + Contract
python3 -m clada                  # Start Gateway
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

```bash
python3 -m clada status             # Show system state
python3 -m clada validate contract  # Validate docs/spec/contract.json
python3 -m clada validate dr <file> # Validate a DR-xxx.md file
python3 -m clada validate all       # Validate all DRs
python3 -m clada index rebuild      # Rebuild L2 index.json
python3 -m clada cold-start         # Scan repo → architecture.md
```

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
