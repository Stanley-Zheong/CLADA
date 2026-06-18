# CLADA examples

A reproducible demo of the CLADA runtime safety harness — the wedge CLADA
ships today: supervise an agent process with `clada run`, block writes to
protected paths, and produce a session log, an audit report, and a path-scoped
rollback plan.

The demo uses **no LLM and no network**. The "agent" is a shell script, so the
whole thing runs offline with zero API cost.

## Files

| File | Role |
|------|------|
| `protected_write_demo.sh` | The driver. Sets up a disposable guardrail fixture, runs the agent under `clada run`, then prints the JSONL log, audit report, and rollback plan. Cleans up on exit. |
| `agent_sim.sh` | The supervised "agent". Performs one legitimate write and two writes CLADA should block. Stands in for `claude` / `codex`. |

## Run it

```bash
# from the repo root, after: pip install -e .
bash examples/protected_write_demo.sh
```

What you should see (full annotated walkthrough in [`docs/demo.md`](../docs/demo.md)):

1. `clada run` hardens `docs/decisions/` to read-only and starts `fswatch`.
2. The agent's legitimate write to `examples/_demo_artifact.txt` succeeds.
3. The agent's two attempts to tamper with `docs/decisions/` are **denied**
   (`Permission denied`).
4. The session JSONL log is written to `runtime/sessions/<session-id>.jsonl`.
5. An audit report is written to `runtime/audits/<session-id>.md`, listing the
   one session-owned file and a rollback command scoped to it.
6. The demo rolls back that artifact and cleans up the fixture.

## Safety

- The only protected path the demo creates (`docs/decisions/`) is already in
  `.gitignore` and is removed on exit.
- The single legitimate artifact (`examples/_demo_artifact.txt`) is rolled back
  at the end of the run.
- Rollback is **path-scoped**: the audit plan only ever targets files the
  session itself created or changed, never pre-existing working-tree edits.

## Swapping in a real agent

`clada run -- <command>` supervises any command. To supervise a real coding
agent instead of the simulator:

```bash
clada run -- claude        # requires: npm install -g @anthropic-ai/claude-code
clada run -- codex         # requires the Codex CLI on PATH
```

The session log, audit report, and rollback plan are produced identically —
only the supervised argv changes.

## Known limitations

`chmod` + `fswatch` is a speed-bump, not a sandbox. See
[`docs/known-limitations.md`](../docs/known-limitations.md) for the macOS
semantics, the fail-closed behavior when `fswatch` is missing, and what is
explicitly deferred.
