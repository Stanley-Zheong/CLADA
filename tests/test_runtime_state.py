"""
Tests for corrupt / malformed runtime-state handling (TEST-06).

CLADA must degrade gracefully when ``runtime/current_state.json`` is damaged
(truncated write, manual edit, partial crash). The supervisor and the runtime
loader must fall back to safe defaults instead of crashing — otherwise a single
bad file would brick every subsequent `clada run`.

Persistence paths are module-level globals, so each test redirects them to a
temporary directory via monkeypatch to avoid touching the real repository.
"""

import json

import pytest

import clada.orchestrator as orch
from clada.orchestrator import RuntimeState, State
from clada.session import SessionSupervisor


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect runtime persistence into tmp_path; return that runtime dir."""
    rt_dir = tmp_path / "runtime"
    rt_dir.mkdir()
    monkeypatch.setattr(orch, "RUNTIME_DIR", rt_dir)
    monkeypatch.setattr(orch, "STATE_FILE", rt_dir / "current_state.json")
    return rt_dir


def test_corrupt_state_falls_back_to_idle(sandbox):
    """Garbage JSON in the state file must not raise — defaults are restored."""
    orch.STATE_FILE.write_text("{ this is not valid json :::", encoding="utf-8")
    rt = RuntimeState()  # must not raise
    assert rt.state == State.IDLE
    assert rt.quota_remaining == orch.QUOTA_DEFAULT
    assert rt.iteration_id == "IT-000"


def test_empty_state_file_falls_back_to_idle(sandbox):
    orch.STATE_FILE.write_text("", encoding="utf-8")
    rt = RuntimeState()
    assert rt.state == State.IDLE


def test_state_with_unknown_enum_value_falls_back(sandbox):
    """A syntactically valid file with a bogus state value still degrades safely."""
    orch.STATE_FILE.write_text(
        json.dumps({"state": "TOTALLY_NOT_A_STATE"}), encoding="utf-8"
    )
    rt = RuntimeState()
    assert rt.state == State.IDLE


def test_runtime_recovers_and_can_persist_after_corruption(sandbox):
    """After loading from a corrupt file, the runtime can still save valid state."""
    orch.STATE_FILE.write_text("not json", encoding="utf-8")
    rt = RuntimeState()
    assert rt.transition(State.PROPOSING) is True
    saved = json.loads(orch.STATE_FILE.read_text(encoding="utf-8"))
    assert saved["state"] == State.PROPOSING.value


def test_run_session_works_despite_corrupt_runtime_state(sandbox, tmp_path):
    """`clada run -- echo ok` must succeed even if runtime state is corrupt."""
    orch.STATE_FILE.write_text("{{{corrupt", encoding="utf-8")
    runtime = RuntimeState()  # degrades to IDLE without raising

    sessions_dir = tmp_path / "sessions"
    sup = SessionSupervisor(
        ["echo", "ok"],
        runtime=runtime,
        sessions_dir=sessions_dir,
        echo=False,
        enforce_policy=False,
    )
    exit_code = sup.run()

    assert exit_code == 0
    assert sup.log_path.exists()
    events = [
        json.loads(ln)
        for ln in sup.log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert events[0]["event"] == "session_start"
    assert events[-1]["event"] == "session_end"
    assert events[-1]["exit_status"] == 0
