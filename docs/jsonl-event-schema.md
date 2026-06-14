# CLADA session JSONL event schema

Every `clada run` session writes a structured event log to
`runtime/sessions/<session-id>.jsonl`. The format is **JSON Lines**: one JSON
object per line, appended as the session progresses and flushed on every write
so a crashed session still leaves a readable partial log.

This document is the authoritative field reference. It is generated from, and
matches, the emitter in `src/clada/session.py`.

## Common fields

Every record carries these three fields:

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | Local timestamp, ISO 8601 (`datetime.now().isoformat()`). |
| `session_id` | string | Session id, e.g. `sess-20260615-065300-cc2bc404`. Stable for all events in one run. |
| `event` | string | The event type — one of the names in the next section. |

Event-specific fields are documented per event below.

## Redaction

CLADA redacts before it writes (omission-preferred — secret values are removed,
not masked):

- `command` / `argv` / `process_output.data` are passed through
  `redact_text()`, which strips PEM private-key blocks, JWTs, known token
  prefixes (OpenAI/Stripe `sk-`/`pk-`, GitHub `ghp_…`, Slack `xox…`, AWS
  `AKIA…`, Google `AIza…`), and `KEY=value` assignments whose key names a
  credential.
- User-supplied paths in `policy_violation` are passed through `redact_path()`,
  which replaces a secret-bearing path (e.g. `.env`, `*.pem`) with
  `[redacted-sensitive-path]`.
- `hardened_paths` and similar fields in policy-status events are CLADA's own
  guardrail directory names (not user secrets), so they are logged verbatim.

## Event types

The typical happy-path order is:
`session_start → command → policy_enforced → process_start → process_output* →
process_exit → checkpoint → session_end`.

### `session_start`

Emitted once at the start of a run.

| Field | Type | Description |
|-------|------|-------------|
| `command` | string[] | Redacted argv of the supervised command. |
| `cwd` | string | Working directory the session ran in. |
| `clada_version` | string | Installed CLADA version, or `"unknown"`. |
| `supervisor_pid` | number | PID of the `clada` supervisor process. |

```json
{"ts":"2026-06-15T06:53:00.501","session_id":"sess-20260615-065300-cc2bc404","event":"session_start","command":["bash","examples/agent_sim.sh"],"cwd":"/path/to/CLADA","clada_version":"1.0.0","supervisor_pid":50640}
```

### `command`

The explicit record of what CLADA was asked to run (RUN-02).

| Field | Type | Description |
|-------|------|-------------|
| `argv` | string[] | Redacted argv of the supervised command. |

```json
{"ts":"2026-06-15T06:53:00.502","session_id":"sess-20260615-065300-cc2bc404","event":"command","argv":["bash","examples/agent_sim.sh"]}
```

### `policy_enforced`

Emitted when enforcement is fully intact (chmod hardening stuck and `fswatch`
is available). Carries the serialized `EnforcementStatus`.

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | `"enforced"`. |
| `degraded` | bool | `false`. |
| `degraded_reasons` | string[] | Empty. |
| `hardened_paths` | string[] | Guardrail dirs made read-only this run. |
| `chmod_failures` | string[] | Guardrail dirs chmod could not lock (empty here). |
| `restore_failures` | string[] | Dirs whose permissions could not be restored (empty here). |
| `fswatch_available` | bool | Whether the `fswatch` binary was found. |
| `fswatch_active` | bool | Whether the monitor thread is live. `false` in the status emitted before the child starts; a later `policy_*` event reflects the active monitor. |

```json
{"ts":"2026-06-15T06:53:00.512","session_id":"sess-20260615-065300-cc2bc404","event":"policy_enforced","mode":"enforced","degraded":false,"degraded_reasons":[],"hardened_paths":["docs/decisions","runtime"],"chmod_failures":[],"restore_failures":[],"fswatch_available":true,"fswatch_active":false}
```

### `policy_degraded`

Same fields as `policy_enforced`, but emitted when enforcement is only partial
(`mode` is `"degraded"` or `"disabled"`). `degraded` is `true` and
`degraded_reasons` explains why.

```json
{"ts":"2026-06-15T06:53:00.512","session_id":"sess-20260615-065300-cc2bc404","event":"policy_degraded","mode":"degraded","degraded":true,"degraded_reasons":["fswatch not installed — write monitoring unavailable"],"hardened_paths":["docs/decisions"],"chmod_failures":[],"restore_failures":[],"fswatch_available":false,"fswatch_active":false}
```

### `policy_blocked`

Emitted when enforcement is degraded and CLADA fails closed — it refuses to
launch the agent. The session ends with exit status `78`.

| Field | Type | Description |
|-------|------|-------------|
| `reason` | string | Human-readable explanation of the block. |
| `requires_owner_approval` | bool | Whether explicit owner approval would unblock the run. |

```json
{"ts":"2026-06-15T06:53:00.514","session_id":"sess-20260615-065300-cc2bc404","event":"policy_blocked","reason":"degraded enforcement: fswatch not installed — write monitoring unavailable (failing closed; owner approval required to proceed)","requires_owner_approval":true}
```

### `policy_violation`

Emitted when the `fswatch` monitor detects an unauthorised write to a watched
directory while the agent is running.

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | The written path, redacted if it is secret-bearing. |
| `rule` | string | Write classification: `"forbidden"`, `"allowed"`, or `"unknown"`. |
| `action` | string | `"kill"` if CLADA killed the writer, else `"log"`. |
| `killed` | bool | Whether the writing process was killed. |

```json
{"ts":"2026-06-15T06:53:00.700","session_id":"sess-20260615-065300-cc2bc404","event":"policy_violation","path":"docs/decisions/DR-666-rogue.md","rule":"forbidden","action":"kill","killed":true}
```

> Note: in the bundled demo, `chmod 555` hardening blocks the protected writes
> outright, so they never reach the filesystem and no `policy_violation` fires.
> Violations surface in cases where `chmod` did not stick but `fswatch` is live
> (e.g. a file created before the run, or a filesystem that ignores `chmod`).

### `process_start`

The supervised child has been forked under a PTY.

| Field | Type | Description |
|-------|------|-------------|
| `pid` | number | PID of the supervised child process. |

```json
{"ts":"2026-06-15T06:53:00.520","session_id":"sess-20260615-065300-cc2bc404","event":"process_start","pid":50646}
```

### `process_output`

A chunk of the child's terminal output. Because a PTY merges stdout and stderr
into one stream, `stream` is always `"stdout"`.

| Field | Type | Description |
|-------|------|-------------|
| `stream` | string | Always `"stdout"` (PTY limitation; see below). |
| `data` | string | Redacted output chunk. May contain `\r\n` line endings from the PTY. |

```json
{"ts":"2026-06-15T06:53:00.560","session_id":"sess-20260615-065300-cc2bc404","event":"process_output","stream":"stdout","data":"[agent] done\r\n"}
```

### `process_exit`

The supervised child has exited.

| Field | Type | Description |
|-------|------|-------------|
| `pid` | number | PID of the child. |
| `exit_status` | number | The child's exit code. |

```json
{"ts":"2026-06-15T06:53:00.890","session_id":"sess-20260615-065300-cc2bc404","event":"process_exit","pid":50646,"exit_status":0}
```

### `checkpoint`

The git diff between the pre-session and post-session working tree, separating
session-owned changes from pre-existing owner work.

| Field | Type | Description |
|-------|------|-------------|
| `git_available` | bool | Whether `git status` succeeded both before and after. |
| `session_owned_paths` | string[] | Files created or changed during the session. |
| `pre_existing_dirty_paths` | string[] | Files already dirty before the session; preserved. |
| `resolved_pre_existing_paths` | string[] | Files that were dirty before but are clean after. |
| `error` | string \| null | Error string if git status failed. |

```json
{"ts":"2026-06-15T06:53:00.900","session_id":"sess-20260615-065300-cc2bc404","event":"checkpoint","git_available":true,"session_owned_paths":["examples/_demo_artifact.txt"],"pre_existing_dirty_paths":["examples/agent_sim.sh"],"resolved_pre_existing_paths":[],"error":null}
```

### `session_end`

The final record of every run.

| Field | Type | Description |
|-------|------|-------------|
| `exit_status` | number | Final exit status reported to the caller. |
| `duration_seconds` | number | Wall-clock session duration. |
| `ok` | bool | `true` iff `exit_status == 0`. |

```json
{"ts":"2026-06-15T06:53:00.901","session_id":"sess-20260615-065300-cc2bc404","event":"session_end","exit_status":0,"duration_seconds":0.3,"ok":true}
```

### `error`

Emitted on a usage error, a failure to start the command, or an unexpected
supervisor error. May appear instead of (or alongside) the normal flow.

| Field | Type | Description |
|-------|------|-------------|
| `message` | string | What went wrong. |
| `hint` | string | (optional) Actionable setup guidance, e.g. how to install the missing executable. |

```json
{"ts":"2026-06-15T06:53:00.530","session_id":"sess-20260615-064500-aaaa1111","event":"error","message":"Command not found: claude","hint":"Install the Claude Code CLI: npm install -g @anthropic-ai/claude-code"}
```

## Exit codes

`clada run` returns the supervised child's exit code on the happy path, plus
these CLADA-specific codes:

| Code | Meaning |
|------|---------|
| `0` | Session completed; child exited `0`. |
| (child code) | Child exited non-zero; that code is propagated. |
| `2` | Usage error — no command supplied after `--`. |
| `78` | Policy blocked — enforcement degraded and CLADA failed closed. |
| `127` | Command not found / failed to start. |

## Reading a log

```bash
# pretty-print every event
python3 -c 'import json,sys; [print(json.dumps(json.loads(l),indent=2)) for l in open(sys.argv[1]) if l.strip()]' \
  runtime/sessions/<session-id>.jsonl

# just the event names, in order
python3 -c 'import json,sys; [print(json.loads(l)["event"]) for l in open(sys.argv[1]) if l.strip()]' \
  runtime/sessions/<session-id>.jsonl
```

## Stream separation caveat

A pseudo-terminal merges the child's stderr into the same stream as stdout, so
all `process_output` events are tagged `stream="stdout"`. True stream
separation would require pipe-based capture and is **deferred** — it is not a
current guarantee.
