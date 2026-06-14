#!/usr/bin/env bash
#
# CLADA protected-write demo.
#
# Runs a simulated agent under `clada run` and shows the four things a CLADA
# session produces: live output, the JSONL session log, the generated audit
# report, and the path-scoped rollback plan.
#
# Safe by construction:
#   * The "agent" is a shell script (examples/agent_sim.sh) — no LLM, no network.
#   * The guardrail fixture it tries to tamper with (docs/decisions/) is created
#     by this demo and removed on exit; that path is already in .gitignore.
#   * The single legitimate artifact it writes is rolled back at the end.
#
# Prerequisite:  pip install -e .   (so the `clada` command is available and
# CLADA_ROOT resolves to this repository).
#
# Run from anywhere inside the repo:
#   bash examples/protected_write_demo.sh

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 1

GUARDRAIL_DIR="docs/decisions"
GUARDRAIL_FILE="$GUARDRAIL_DIR/DR-000-demo.md"
ARTIFACT="examples/_demo_artifact.txt"

cleanup() {
  # Restore writability before removing the read-only guardrail fixture.
  [ -d "$GUARDRAIL_DIR" ] && chmod -R u+w "$GUARDRAIL_DIR" 2>/dev/null
  rm -f "$GUARDRAIL_FILE" "$GUARDRAIL_DIR/DR-666-rogue.md" 2>/dev/null
  rmdir "$GUARDRAIL_DIR" 2>/dev/null
  rm -f "$ARTIFACT" 2>/dev/null
}
trap cleanup EXIT

if ! command -v clada >/dev/null 2>&1; then
  echo "clada not found on PATH. Install it first:"
  echo "    python3 -m venv .venv && source .venv/bin/activate"
  echo "    pip install -e ."
  exit 1
fi

echo "==> Preparing a disposable guardrail fixture: $GUARDRAIL_FILE"
mkdir -p "$GUARDRAIL_DIR"
printf '# DR-000: demo guardrail record\nCLADA protects this file from the agent.\n' > "$GUARDRAIL_FILE"
chmod -R u+w "$GUARDRAIL_DIR"

echo
echo "==> Supervising the agent:  clada run -- bash examples/agent_sim.sh"
echo "----------------------------------------------------------------------"
clada run -- bash examples/agent_sim.sh
RUN_EXIT=$?
echo "----------------------------------------------------------------------"
echo "==> clada run exited: $RUN_EXIT"

if [ "$RUN_EXIT" -eq 78 ]; then
  echo
  echo "NOTE: exit 78 means CLADA refused to launch the agent because policy"
  echo "      enforcement was degraded (commonly: fswatch not installed, or"
  echo "      chmod hardening did not stick). This is the fail-closed safety"
  echo "      path — see docs/known-limitations.md. Install fswatch and retry:"
  echo "          brew install fswatch"
fi

LOG="$(ls -t runtime/sessions/*.jsonl 2>/dev/null | head -1)"
REPORT="$(ls -t runtime/audits/*.md 2>/dev/null | head -1)"

echo
echo "==> Session JSONL log: $LOG"
if [ -n "$LOG" ]; then
  echo "    Events in order:"
  python3 - "$LOG" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    e = json.loads(line)
    print("      -", e.get("event"))
PY
fi

echo
echo "==> Audit report: $REPORT"
[ -n "$REPORT" ] && sed -n '1,80p' "$REPORT"

echo
echo "==> Rollback"
echo "    The audit report's commands are scoped to session-owned files only —"
echo "    they never touch pre-existing changes. Demonstrating rollback of the"
echo "    one legitimate artifact ($ARTIFACT):"
if [ -f "$ARTIFACT" ]; then
  echo "      \$ git clean -f -- $ARTIFACT"
  git clean -f -- "$ARTIFACT" >/dev/null 2>&1 || rm -f "$ARTIFACT"
  echo "      -> removed; working tree clean again."
else
  echo "      (no artifact to roll back — the agent did not run)"
fi

echo
echo "==> Demo complete. Guardrail fixture and artifacts cleaned up."
