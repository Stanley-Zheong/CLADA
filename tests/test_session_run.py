"""
Tests for the Phase 2 Session Supervisor (`clada run`).

These tests drive :class:`clada.session.SessionSupervisor` directly with a
sandboxed ``sessions_dir`` (pytest ``tmp_path``) so the real repository
``runtime/sessions/`` is never touched. Output echo is disabled to keep the
test output clean.

Covers RUN-01..RUN-04, LOG-01..LOG-02 and the TEST-02 smoke path
(`clada run -- echo ok`).
"""

import json

from clada.session import (
    EXIT_COMMAND_NOT_FOUND,
    SessionSupervisor,
    new_session_id,
)


def _read_events(log_path):
    """Parse a JSONL session log into a list of event dicts.

    Also asserts the LOG-02 invariant: exactly one JSON object per line.
    """
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))  # raises if a line is not valid JSON
    return events


def test_session_id_is_unique_and_prefixed():
    a, b = new_session_id(), new_session_id()
    assert a.startswith("sess-")
    assert a != b


def test_run_echo_ok_smoke(tmp_path):
    """TEST-02: `clada run -- echo ok` succeeds and logs a full session."""
    sup = SessionSupervisor(["echo", "ok"], sessions_dir=tmp_path, echo=False)
    exit_code = sup.run()

    assert exit_code == 0
    assert sup.log_path.exists()
    assert sup.log_path.suffix == ".jsonl"

    events = _read_events(sup.log_path)
    kinds = [e["event"] for e in events]

    # Lifecycle events are all present and ordered (RUN-03, LOG-01).
    assert kinds[0] == "session_start"
    assert "command" in kinds
    assert "process_start" in kinds
    assert "process_output" in kinds
    assert "process_exit" in kinds
    assert kinds[-1] == "session_end"

    # Every record carries the session id and a timestamp.
    for e in events:
        assert e["session_id"] == sup.session_id
        assert "ts" in e

    # The child's stdout actually made it into the log.
    output = "".join(e["data"] for e in events if e["event"] == "process_output")
    assert "ok" in output

    # session_end records success, exit status and a numeric duration.
    end = events[-1]
    assert end["exit_status"] == 0
    assert end["ok"] is True
    assert isinstance(end["duration_seconds"], (int, float))


def test_run_propagates_nonzero_exit(tmp_path):
    """RUN-04: a failing command surfaces its non-zero exit status."""
    sup = SessionSupervisor(
        ["python3", "-c", "import sys; sys.exit(3)"],
        sessions_dir=tmp_path,
        echo=False,
    )
    exit_code = sup.run()
    assert exit_code == 3

    events = _read_events(sup.log_path)
    exit_events = [e for e in events if e["event"] == "process_exit"]
    assert exit_events and exit_events[-1]["exit_status"] == 3
    assert events[-1]["event"] == "session_end"
    assert events[-1]["exit_status"] == 3
    assert events[-1]["ok"] is False


def test_run_missing_command_reports_guidance(tmp_path):
    """RUN-04: an unknown executable is rejected with actionable guidance."""
    sup = SessionSupervisor(
        ["clada-no-such-binary-xyz"], sessions_dir=tmp_path, echo=False
    )
    exit_code = sup.run()
    assert exit_code == EXIT_COMMAND_NOT_FOUND

    events = _read_events(sup.log_path)
    errors = [e for e in events if e["event"] == "error"]
    assert errors, "an error event must be logged for a missing command"
    assert "not found" in errors[0]["message"].lower()
    # Guidance must be present and non-empty.
    assert errors[0]["hint"]
    # No child should have been started.
    assert not any(e["event"] == "process_start" for e in events)
    assert events[-1]["event"] == "session_end"


def test_run_logs_one_json_object_per_line(tmp_path):
    """LOG-02: the log is strict JSONL — one object per line, all parseable."""
    sup = SessionSupervisor(["echo", "hello"], sessions_dir=tmp_path, echo=False)
    sup.run()

    raw = sup.log_path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert lines, "log must not be empty"
    for ln in lines:
        # Each line independently parses to a dict (not an array / scalar).
        obj = json.loads(ln)
        assert isinstance(obj, dict)
        assert "event" in obj


def test_run_creates_sessions_dir_if_absent(tmp_path):
    """The supervisor creates runtime/sessions/ on demand."""
    nested = tmp_path / "runtime" / "sessions"
    assert not nested.exists()
    sup = SessionSupervisor(["echo", "ok"], sessions_dir=nested, echo=False)
    sup.run()
    assert nested.is_dir()
    assert sup.log_path.exists()
