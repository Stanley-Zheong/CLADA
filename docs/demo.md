# CLADA demo: supervising a protected write

This walkthrough documents the demo in [`examples/`](../examples/) end to end:
the expected terminal output, the JSONL session log, the audit report, and the
rollback path. It is the concrete proof of the CLADA wedge — a runtime safety
harness for AI coding agents — and every output below is reproducible offline,
with no LLM and no API cost.

## What the demo does

`examples/protected_write_demo.sh` runs a shell "agent"
(`examples/agent_sim.sh`) under `clada run`. The agent makes three writes:

1. A **legitimate** write to `examples/_demo_artifact.txt` (succeeds).
2. A **tamper** attempt appending to `docs/decisions/DR-000-demo.md` (blocked).
3. A **plant** attempt creating `docs/decisions/DR-666-rogue.md` (blocked).

CLADA hardens `docs/decisions/` to read-only (`chmod 555`) and starts an
`fswatch` monitor before the agent runs, so the two protected writes fail with
`Permission denied`.

## Run it

```bash
# from the repo root, after: pip install -e .
bash examples/protected_write_demo.sh
```

## Expected terminal output

The exact session id, pid, and timestamps vary per run; everything else is
stable.

```text
==> Preparing a disposable guardrail fixture: docs/decisions/DR-000-demo.md

==> Supervising the agent:  clada run -- bash examples/agent_sim.sh
----------------------------------------------------------------------
06:53:00 [PROXY] Protected paths hardened (chmod 555)
06:53:00 [PTY] sess-20260615-065300-cc2bc404 started (pid=50646)
06:53:00 [PROXY] fswatch monitoring started
[agent] starting simulated work session
[agent] writing a legitimate artifact -> examples/_demo_artifact.txt
[agent] attempting to tamper with a guardrail file -> docs/decisions/DR-000-demo.md
examples/agent_sim.sh: line 25: docs/decisions/DR-000-demo.md: Permission denied
[agent] -> tamper BLOCKED by CLADA (permission denied)
[agent] attempting to plant a new file -> docs/decisions/DR-666-rogue.md
examples/agent_sim.sh: line 33: docs/decisions/DR-666-rogue.md: Permission denied
[agent] -> create BLOCKED by CLADA (permission denied)
[agent] done

[clada] session sess-20260615-065300-cc2bc404 exited 0 → runtime/sessions/sess-20260615-065300-cc2bc404.jsonl
----------------------------------------------------------------------
==> clada run exited: 0
```

The two `Permission denied` lines are CLADA's `chmod 555` hardening stopping
the agent from rewriting its own guardrails. The legitimate write to
`examples/` is untouched.

## The session log (JSONL)

Each run appends one JSON object per line to
`runtime/sessions/<session-id>.jsonl`. The demo prints the event sequence:

```text
==> Session JSONL log: runtime/sessions/sess-20260615-065300-cc2bc404.jsonl
    Events in order:
      - session_start
      - command
      - policy_enforced
      - process_start
      - process_output
      - process_exit
      - checkpoint
      - session_end
```

A representative `policy_enforced` record (pretty-printed) — note the
`hardened_paths` and `fswatch_available` fields that make the enforcement state
auditable rather than assumed:

```json
{
  "ts": "2026-06-15T06:53:00.512345",
  "session_id": "sess-20260615-065300-cc2bc404",
  "event": "policy_enforced",
  "mode": "enforced",
  "degraded": false,
  "degraded_reasons": [],
  "hardened_paths": ["docs/decisions", "runtime"],
  "chmod_failures": [],
  "restore_failures": [],
  "fswatch_available": true,
  "fswatch_active": false
}
```

The full field-by-field contract for every event type is in
[`docs/jsonl-event-schema.md`](./jsonl-event-schema.md).

## The audit report

After the session ends, CLADA writes a human-readable audit report to
`runtime/audits/<session-id>.md`:

````markdown
# CLADA Session Audit: sess-20260615-065300-cc2bc404

- Generated: 2026-06-15T06:53:01.779702
- Event log: `runtime/sessions/sess-20260615-065300-cc2bc404.jsonl`
- Command: `bash examples/agent_sim.sh`
- Exit status: `0`
- OK: `True`
- Duration seconds: `0.3`

## Checkpoint

- Git available: `True`
- Session-owned changed files: `1`
- Pre-existing dirty files preserved: `2`

### Session-Owned Paths

- `examples/_demo_artifact.txt`

### Pre-Existing Dirty Paths

- `examples/agent_sim.sh`
- `examples/protected_write_demo.sh`

## Policy Events

- Policy events recorded: `1`
- Violations recorded: `0`
- Modes observed: `enforced`

## Rollback Instructions

These commands are intentionally path-scoped to the files detected as session-owned.
Review them before running; they do not target pre-existing dirty files.

```bash
git clean -f -- 'examples/_demo_artifact.txt'
```

## Notes

- The report lists paths and event metadata only; it does not include file contents.
- Secret-like command/output values are inherited from the redacted JSONL event log.
````

The key safety property is visible in the **Checkpoint** section: the demo's
own uncommitted scripts (`agent_sim.sh`, `protected_write_demo.sh`) appear under
**Pre-Existing Dirty Paths** and are *preserved*. Only
`examples/_demo_artifact.txt`, which the session actually created, is listed as
**session-owned** and targeted by rollback. CLADA never treats the whole
working tree as session-owned.

## The rollback path

The rollback commands come straight from the audit report and are scoped to
session-owned files. The demo executes the one applicable command:

```bash
git clean -f -- 'examples/_demo_artifact.txt'
```

- **Untracked** session-owned files → `git clean -f -- <paths>`.
- **Tracked** session-owned files → `git restore --staged -- <paths>` followed
  by `git restore -- <paths>`.
- If git is unavailable, the report says so and asks you to inspect the working
  tree manually rather than guessing.

Pre-existing dirty files are never included in any rollback command.

## If `clada run` exits 78

Exit code `78` means CLADA refused to launch the agent because policy
enforcement was **degraded** — most commonly because `fswatch` is not installed,
or `chmod` hardening did not stick on the filesystem. This is the deliberate
fail-closed path: CLADA will not run an agent while pretending to protect paths
it cannot. Install `fswatch` (`brew install fswatch`) and re-run, or see
[`docs/known-limitations.md`](./known-limitations.md) for the owner-approval
override and the full degraded-mode contract.
