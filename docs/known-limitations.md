# CLADA known limitations

CLADA's runtime protection is built on `chmod` hardening and `fswatch`
monitoring. This is an honest **speed-bump**, not a sandbox or a syscall jail.
This document states exactly what that protection does and does not give you,
focusing on macOS semantics, and lists what is explicitly deferred.

> Design rule (from the project epic): **do not claim hardened sandboxing for
> `chmod`/`fswatch` behavior unless tests prove it.** Everything below reflects
> what the code and `tests/` suite actually demonstrate.

## chmod hardening

Before launching the agent, CLADA runs `chmod -R 555` on its guardrail
directories (`docs/decisions`, `runtime`, `.comm` — those that exist) and
restores `755` afterward.

What this gives you:

- A genuine permission barrier: on a normal local filesystem, the agent's
  attempts to write into a hardened directory fail with `Permission denied`.
  This is the mechanism the [demo](./demo.md) shows blocking two writes.

Limitations:

- **Not a jail.** `chmod` is advisory file-mode enforcement. A process running
  as a user who can `chmod` the directory back (including the agent, if it is
  the same user) can lift the restriction. CLADA does not run the agent as a
  separate, lower-privileged user.
- **Pre-existing files.** `chmod 555` on a directory blocks creating, renaming,
  and deleting entries, but the mode of files already inside governs whether
  their *contents* can be rewritten. Hardening is best-effort over the tree, not
  a per-inode guarantee.
- **Filesystem variance.** Some filesystems (certain network mounts, mounts with
  `noexec`/`noperm`-style options, or case-insensitive overlays) do not honour
  Unix permission bits faithfully. When a `chmod` returns non-zero, CLADA
  records it in `chmod_failures` and treats enforcement as degraded rather than
  pretending it worked.
- **Restore is best-effort.** If restoring `755` fails on exit, CLADA records a
  `restore_failures` entry; a guardrail directory could be left read-only and
  need a manual `chmod -R u+w`.
- **macOS SIP / LD_PRELOAD (RISK-04).** Full file-access interception via
  `LD_PRELOAD` is unavailable for System-Integrity-Protected processes on macOS,
  which is why CLADA falls back to `chmod` rather than syscall interception.

## fswatch monitoring

When `fswatch` is installed, CLADA starts a recursive monitor on the watch
directories (`docs/decisions`, `runtime`) for `Created`/`Updated` events. On an
unauthorised write it logs a redacted `policy_violation` event and, if it knows
the agent's pid, kills the writer.

Limitations:

- **Detection, not prevention.** `fswatch` reports a write *after* it lands. The
  kill is reactive; a fast write-then-exit can complete before the monitor acts.
  `chmod` hardening is the primary defence; `fswatch` is the backstop (RISK-02).
- **Optional dependency.** `fswatch` is not bundled. Without it, write
  monitoring is unavailable and enforcement is degraded (see fail-closed below).
- **Capture-rate caveats (RISK-02).** `fswatch` behaviour on bind mounts and
  some virtualised filesystems is not empirically verified here; do not assume
  100% capture.
- **No stream separation.** Unrelated to fswatch, but worth noting for log
  consumers: the PTY merges the agent's stdout and stderr, so all
  `process_output` events are tagged `stream="stdout"`.

## Fail-closed behaviour (no live owner prompt)

`clada run` is non-interactive — there is no live Owner to approve a degraded
run. So when enforcement is degraded (e.g. `fswatch` missing, or `chmod`
hardening did not stick), CLADA **fails closed**: it emits `policy_blocked` and
exits `78` instead of launching the agent under the illusion of protection.

To proceed anyway you must explicitly opt into reduced protection. In code, the
gate is `clada.policy.evaluate_enforcement(status, owner_approved=True)` or
`fail_closed=False`; there is intentionally **no** silent fail-open default.
Install `fswatch` (`brew install fswatch` on macOS) to run fully enforced.

## Rollback

Rollback guidance is generated from a git checkpoint diff and is **path-scoped
to session-owned files only** — it never targets pre-existing working-tree
edits. Limitations:

- **Requires git.** If `git status` is unavailable, the audit report says so and
  asks you to inspect the tree manually instead of emitting commands.
- **Advisory, not automatic.** `clada run` prints the rollback commands in the
  audit report; it does not auto-revert. You review and run them.
- **Untracked vs tracked** are handled differently: untracked session-owned
  files get `git clean -f`, tracked ones get `git restore --staged` +
  `git restore`.

## Explicitly deferred

These appear in the broader CLADA design but are **not** shipping behaviour
today. They are documented as future scope, not current guarantees:

- Process suspension/resume of the agent (`SIGSTOP`/`SIGCONT`) for live review
  (RISK-01: long suspensions risk API TCP timeouts).
- Docker / container test isolation.
- True per-process file-access sandboxing (LD_PRELOAD / syscall interception).
- The full interactive Gateway state machine, Dual-Lock contract generation, and
  the L3 historical memory tier — see the Implementation Phases table in the
  README.
- stderr/stdout stream separation in the session log.

## Where this is enforced in code

| Concern | Source |
|---------|--------|
| Policy model, degraded status, fail-closed decision, redaction | `src/clada/policy.py` |
| chmod hardening, fswatch monitoring, violation handling | `src/clada/orchestrator.py` (`FileAccessProxy`) |
| Session supervision, JSONL events, policy gate | `src/clada/session.py` |
| Audit report + rollback commands | `src/clada/audit.py`, `src/clada/checkpoint.py` |
| Tests covering the above | `tests/test_policy.py`, `tests/test_session_run.py`, `tests/test_audit.py`, `tests/test_checkpoint.py` |
