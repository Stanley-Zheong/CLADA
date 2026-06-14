#!/usr/bin/env python3
"""Audit report generation for supervised CLADA sessions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from clada.checkpoint import CheckpointDiff


DEFAULT_AUDITS_DIR = Path(__file__).parent.parent.parent / "runtime" / "audits"


def read_events(log_path: Path) -> List[dict]:
    events: List[dict] = []
    if not Path(log_path).exists():
        return events
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def generate_audit_report(
    *,
    session_id: str,
    log_path: Path,
    checkpoint: CheckpointDiff,
    audits_dir: Path | None = None,
) -> Path:
    audits = Path(audits_dir) if audits_dir else DEFAULT_AUDITS_DIR
    audits.mkdir(parents=True, exist_ok=True)
    report_path = audits / f"{session_id}.md"

    events = read_events(log_path)
    command = _first(events, "command", "argv", [])
    end = _last_event(events, "session_end")
    policy_events = [e for e in events if e.get("event", "").startswith("policy_")]
    violations = [e for e in events if e.get("event") == "policy_violation"]

    lines = [
        f"# CLADA Session Audit: {session_id}",
        "",
        f"- Generated: {datetime.now().isoformat()}",
        f"- Event log: `{Path(log_path)}`",
        f"- Command: `{_format_command(command)}`",
        f"- Exit status: `{end.get('exit_status', 'unknown')}`",
        f"- OK: `{end.get('ok', 'unknown')}`",
        f"- Duration seconds: `{end.get('duration_seconds', 'unknown')}`",
        "",
        "## Checkpoint",
        "",
        f"- Git available: `{checkpoint.git_available}`",
        f"- Session-owned changed files: `{len(checkpoint.session_owned_paths)}`",
        f"- Pre-existing dirty files preserved: `{len(checkpoint.pre_existing_dirty_paths)}`",
        "",
        "### Session-Owned Paths",
        "",
        *_bullets(checkpoint.session_owned_paths),
        "",
        "### Pre-Existing Dirty Paths",
        "",
        *_bullets(checkpoint.pre_existing_dirty_paths),
        "",
        "## Policy Events",
        "",
        *_policy_summary(policy_events, violations),
        "",
        "## Rollback Instructions",
        "",
        "These commands are intentionally path-scoped to the files detected as session-owned.",
        "Review them before running; they do not target pre-existing dirty files.",
        "",
        "```bash",
        *checkpoint.rollback_commands(),
        "```",
        "",
        "## Notes",
        "",
        "- The report lists paths and event metadata only; it does not include file contents.",
        "- Secret-like command/output values are inherited from the redacted JSONL event log.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _first(events: Iterable[dict], event: str, field: str, default):
    for item in events:
        if item.get("event") == event:
            return item.get(field, default)
    return default


def _last_event(events: Iterable[dict], event: str) -> dict:
    found = {}
    for item in events:
        if item.get("event") == event:
            found = item
    return found


def _format_command(command) -> str:
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command)


def _bullets(paths: Iterable[str]) -> List[str]:
    items = [f"- `{path}`" for path in paths]
    return items or ["- None"]


def _policy_summary(policy_events: List[dict], violations: List[dict]) -> List[str]:
    if not policy_events:
        return ["- No policy events were recorded."]
    modes = [event.get("mode") for event in policy_events if event.get("mode")]
    lines = [
        f"- Policy events recorded: `{len(policy_events)}`",
        f"- Violations recorded: `{len(violations)}`",
    ]
    if modes:
        lines.append(f"- Modes observed: `{', '.join(sorted(set(modes)))}`")
    return lines
