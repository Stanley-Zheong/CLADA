"""
Tests for the CLADA runtime state machine (RuntimeState.transition).

The RuntimeState class persists to module-level paths (RUNTIME_DIR / STATE_FILE).
To avoid mutating the real repository runtime state, every test redirects those
module globals to a temporary directory via monkeypatch.
"""

import json

import pytest

import clada.orchestrator as orch
from clada.orchestrator import RuntimeState, State


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A fresh RuntimeState whose persistence is sandboxed to tmp_path."""
    rt_dir = tmp_path / "runtime"
    monkeypatch.setattr(orch, "RUNTIME_DIR", rt_dir)
    monkeypatch.setattr(orch, "STATE_FILE", rt_dir / "current_state.json")
    return RuntimeState()


def test_starts_idle(runtime):
    assert runtime.state == State.IDLE


def test_valid_transition_idle_to_proposing(runtime):
    assert runtime.transition(State.PROPOSING) is True
    assert runtime.state == State.PROPOSING


def test_valid_transition_chain(runtime):
    # IDLE → PROPOSING → EXECUTING → AUDITING → PENDING_COMMIT → IDLE
    assert runtime.transition(State.PROPOSING) is True
    assert runtime.transition(State.EXECUTING) is True
    assert runtime.transition(State.AUDITING) is True
    assert runtime.transition(State.PENDING_COMMIT) is True
    assert runtime.transition(State.IDLE) is True
    assert runtime.state == State.IDLE


def test_invalid_transition_rejected(runtime):
    # IDLE may only go to BOOTSTRAP or PROPOSING, never straight to EXECUTING.
    assert runtime.transition(State.EXECUTING) is False
    assert runtime.state == State.IDLE


def test_invalid_transition_does_not_persist(runtime):
    runtime.transition(State.EXECUTING)  # invalid, returns False
    # A rejected transition must not have written a new state to disk.
    if orch.STATE_FILE.exists():
        saved = json.loads(orch.STATE_FILE.read_text())
        assert saved["state"] == State.IDLE.value


def test_valid_transition_persists_to_disk(runtime):
    assert runtime.transition(State.PROPOSING) is True
    assert orch.STATE_FILE.exists()
    saved = json.loads(orch.STATE_FILE.read_text())
    assert saved["state"] == State.PROPOSING.value


def test_terminal_executing_branches(runtime):
    runtime.transition(State.PROPOSING)
    runtime.transition(State.EXECUTING)
    # SUSPENDED is reachable from EXECUTING ...
    assert runtime.transition(State.SUSPENDED) is True
    # ... but PENDING_COMMIT is not reachable from SUSPENDED.
    assert runtime.transition(State.PENDING_COMMIT) is False
    assert runtime.state == State.SUSPENDED
