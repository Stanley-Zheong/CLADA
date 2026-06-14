"""
Tests for the Phase 3 Policy Engine (`clada.policy`) and the refactored
:class:`clada.orchestrator.FileAccessProxy` (POL-01..POL-05, LOG-03, TEST-05).

Safety constraints honoured here:
* No test mutates real project permissions — every chmod runs inside pytest's
  ``tmp_path`` and is restored within the same test.
* No test kills a real or arbitrary process — violation handling is exercised
  with ``executor_pid=None`` so the kill branch is never taken.
* chmod *failure* is simulated by monkeypatching ``subprocess.run`` rather than
  by actually breaking a filesystem.
"""

import json
import os
import stat

import pytest

from clada.policy import (
    PathPolicy,
    EnforcementStatus,
    EnforcementMode,
    EnforcementAction,
    evaluate_enforcement,
    merge_statuses,
    redact_path,
    redact_text,
    looks_like_secret,
)
from clada.orchestrator import FileAccessProxy

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="chmod semantics are POSIX-only")


# ── Policy model (POL-01, POL-02, POL-03) ───────────────────────────
def test_protected_read_matches_secret_files():
    pol = PathPolicy.default()
    assert pol.is_protected_read(".env")
    assert pol.is_protected_read("config/.env.production")
    assert pol.is_protected_read("secrets/db.txt")
    assert pol.is_protected_read("certs/server.pem")
    assert pol.is_protected_read("deploy/id_rsa")
    # Ordinary source is not secret.
    assert not pol.is_protected_read("src/app.py")
    assert not pol.is_protected_read("README.md")


def test_write_classification():
    pol = PathPolicy.default()
    assert pol.classify_write("docs/decisions/DR-1.md") == "forbidden"
    assert pol.classify_write(".git/config") == "forbidden"
    assert pol.classify_write("runtime/current_state.json") == "forbidden"
    assert pol.classify_write("src/app.py") == "allowed"
    assert pol.classify_write("tests/test_x.py") == "allowed"
    # Neither explicitly allowed nor forbidden → unknown, not auto-allowed.
    assert pol.classify_write("random_root_file.txt") == "unknown"


def test_forbidden_wins_over_allowed():
    # A contrived policy where a path is in both sets — forbidden must win.
    pol = PathPolicy(
        write_forbidden_prefixes=("src/secret/",),
        write_allowed_prefixes=("src/",),
    )
    assert pol.is_write_forbidden("src/secret/x.py")
    assert not pol.is_write_allowed("src/secret/x.py")
    assert pol.is_write_allowed("src/ok.py")


# ── EnforcementStatus + decision (POL-04) ───────────────────────────
def _intact_status() -> EnforcementStatus:
    return EnforcementStatus(
        chmod_attempted=True,
        hardened_paths=["runtime"],
        fswatch_attempted=True,
        fswatch_available=True,
        fswatch_active=True,
    )


def test_status_modes():
    intact = _intact_status()
    assert intact.mode is EnforcementMode.ENFORCED
    assert not intact.degraded

    degraded = EnforcementStatus(chmod_attempted=True, hardened_paths=["runtime"])
    degraded.add_reason("fswatch not installed")
    assert degraded.mode is EnforcementMode.DEGRADED
    assert degraded.degraded

    # Nothing stuck at all → DISABLED.
    disabled = EnforcementStatus(chmod_attempted=True, chmod_failures=["runtime"])
    disabled.add_reason("chmod failed on runtime")
    assert disabled.mode is EnforcementMode.DISABLED
    assert disabled.degraded


def test_evaluate_enforcement_fail_closed_and_approval():
    intact = _intact_status()
    d = evaluate_enforcement(intact)
    assert d.action is EnforcementAction.PROCEED
    assert d.allowed

    degraded = EnforcementStatus(chmod_attempted=True, hardened_paths=["runtime"])
    degraded.add_reason("fswatch not installed")

    # Default: fail closed → blocked.
    blocked = evaluate_enforcement(degraded)
    assert blocked.action is EnforcementAction.BLOCK
    assert not blocked.allowed
    assert blocked.requires_owner_approval

    # Explicit owner approval unblocks.
    approved = evaluate_enforcement(degraded, owner_approved=True)
    assert approved.action is EnforcementAction.PROCEED_WITH_APPROVAL
    assert approved.allowed

    # Opt-in fail-open also proceeds, but is still flagged.
    open_mode = evaluate_enforcement(degraded, fail_closed=False)
    assert open_mode.allowed
    assert open_mode.requires_owner_approval


def test_merge_statuses_combines_reasons():
    a = EnforcementStatus(chmod_attempted=True, hardened_paths=["runtime"])
    b = EnforcementStatus(fswatch_attempted=True, fswatch_available=False)
    b.add_reason("fswatch not installed")
    merged = merge_statuses(a, b)
    assert merged.chmod_attempted and merged.fswatch_attempted
    assert "runtime" in merged.hardened_paths
    assert merged.degraded
    assert merged.to_event()["mode"] == "degraded"


# ── chmod failure (TEST-05) ─────────────────────────────────────────
def _make_guardrail_tree(root):
    (root / "runtime").mkdir()
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / ".comm").mkdir()


def test_harden_reports_chmod_failure(tmp_path, monkeypatch):
    """A failing chmod produces a degraded status, not a silent success."""
    _make_guardrail_tree(tmp_path)
    proxy = FileAccessProxy(tmp_path, runtime=None)

    import clada.orchestrator as orch

    class _Result:
        returncode = 1
        stdout = b""
        stderr = b"chmod: operation not permitted"

    monkeypatch.setattr(orch.subprocess, "run", lambda *a, **k: _Result())

    status = proxy.harden_protected_paths()
    assert status.chmod_attempted
    assert not status.hardened_paths
    assert sorted(status.chmod_failures) == sorted(["runtime", "docs/decisions", ".comm"])
    assert status.degraded
    assert status.mode is EnforcementMode.DISABLED
    assert any("chmod failed" in r for r in status.degraded_reasons)


# ── missing fswatch (TEST-05) ───────────────────────────────────────
def test_fswatch_missing_is_degraded(tmp_path, monkeypatch):
    _make_guardrail_tree(tmp_path)
    proxy = FileAccessProxy(tmp_path, runtime=None)
    monkeypatch.setattr(proxy, "fswatch_available", lambda: False)

    status = proxy.start_fswatch(executor_pid=None)
    assert status.fswatch_attempted
    assert not status.fswatch_available
    assert not status.fswatch_active
    assert status.degraded
    assert any("fswatch" in r for r in status.degraded_reasons)


def test_assess_enforcement_gates_on_fswatch(tmp_path, monkeypatch):
    _make_guardrail_tree(tmp_path)
    proxy = FileAccessProxy(tmp_path, runtime=None)
    monkeypatch.setattr(proxy, "fswatch_available", lambda: False)

    harden = EnforcementStatus(chmod_attempted=True, hardened_paths=["runtime"])
    gate = proxy.assess_enforcement(harden)
    assert gate.degraded  # chmod ok but no monitoring → degraded
    decision = evaluate_enforcement(gate)
    assert not decision.allowed  # fail closed


# ── permission restoration (TEST-05) ────────────────────────────────
@POSIX_ONLY
def test_harden_then_restore_roundtrips_permissions(tmp_path):
    """Real chmod, but only inside tmp_path — never the project tree."""
    _make_guardrail_tree(tmp_path)
    target = tmp_path / "runtime"
    (target / "state.json").write_text("{}")
    proxy = FileAccessProxy(tmp_path, runtime=None)

    try:
        harden = proxy.harden_protected_paths()
        assert "runtime" in harden.hardened_paths
        assert not harden.chmod_failures
        mode_locked = stat.S_IMODE(target.stat().st_mode)
        assert not (mode_locked & stat.S_IWUSR), "owner write bit should be cleared"

        restore = proxy.restore_protected_paths()
        assert not restore.restore_failures
        mode_restored = stat.S_IMODE(target.stat().st_mode)
        assert mode_restored & stat.S_IWUSR, "owner write bit should be restored"
    finally:
        # Belt-and-suspenders: ensure tmp tree is writable for cleanup even if
        # an assertion above fired mid-way.
        proxy.restore_protected_paths()


# ── policy-violation event, no process kill (TEST-05, LOG-03) ───────
def test_report_violation_emits_event_without_killing(tmp_path):
    events = []
    proxy = FileAccessProxy(
        tmp_path, runtime=None, event_sink=lambda e, f: events.append((e, f))
    )
    proxy._report_violation("runtime/current_state.json", executor_pid=None)

    assert events, "a policy_violation event must be emitted"
    name, fields = events[-1]
    assert name == "policy_violation"
    assert fields["killed"] is False
    assert fields["action"] == "log"
    assert fields["rule"] == "forbidden"
    assert fields["path"] == "runtime/current_state.json"


def test_report_violation_redacts_secret_path(tmp_path):
    events = []
    proxy = FileAccessProxy(
        tmp_path, runtime=None, event_sink=lambda e, f: events.append((e, f))
    )
    proxy._report_violation(".env", executor_pid=None)
    assert events[-1][1]["path"] == "[redacted-sensitive-path]"


# ── log redaction (POL-05, TEST-05) ─────────────────────────────────
def test_redact_text_omits_secrets():
    # Assignment-style secret: value omitted entirely.
    out = redact_text("PASSWORD=hunter2trustno1")
    assert "hunter2trustno1" not in out
    assert "[REDACTED]" in out

    # Token prefixes.
    assert "[REDACTED]" in redact_text("use ghp_0123456789abcdefghijABCD now")
    assert "[REDACTED]" in redact_text("key sk-0123456789abcdefghijklmnop")

    # JWT.
    jwt = "eyJhbGciOi.eyJzdWIiOmFzZGY.SflKxwRJSMeKKF2QT"
    assert "[REDACTED]" in redact_text(jwt)
    assert jwt not in redact_text(jwt)

    # PEM private key block.
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact_text(pem)

    # Non-secret text is untouched.
    assert redact_text("just a normal log line") == "just a normal log line"
    assert redact_text(None) == ""


def test_looks_like_secret():
    assert looks_like_secret("ghp_0123456789abcdefghij")
    assert looks_like_secret("AKIAIOSFODNN7EXAMPLE")
    assert not looks_like_secret("plain text")
    assert not looks_like_secret("")


def test_redact_path():
    pol = PathPolicy.default()
    assert redact_path(".env", pol) == "[redacted-sensitive-path]"
    assert redact_path("config/.env.production", pol) == "[redacted-sensitive-path]"
    assert redact_path("secrets/prod.json", pol) == "[redacted-sensitive-path]"
    assert redact_path("certs/server.pem", pol) == "[redacted-sensitive-path]"
    # Non-sensitive paths come back as a clean POSIX string.
    assert redact_path("src/app.py", pol) == "src/app.py"
    assert redact_path(None, pol) == ""


# ── session policy events (LOG-03) ──────────────────────────────────
def test_session_emits_policy_events(tmp_path):
    from clada.session import SessionSupervisor

    sup = SessionSupervisor(["echo", "ok"], sessions_dir=tmp_path, echo=False)
    sup._open_log()
    try:
        status = EnforcementStatus(chmod_attempted=True, hardened_paths=["runtime"])
        status.add_reason("fswatch not installed — write monitoring disabled")
        sup.emit_policy_status(status)
        sup.emit_policy_violation("secrets/db.txt", rule="forbidden",
                                  action="kill", killed=True)
    finally:
        sup._close_log()

    events = [
        json.loads(ln)
        for ln in sup.log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    kinds = [e["event"] for e in events]
    assert "policy_degraded" in kinds

    viol = next(e for e in events if e["event"] == "policy_violation")
    assert viol["path"] == "[redacted-sensitive-path]"  # secret path omitted
    assert viol["killed"] is True
    assert viol["rule"] == "forbidden"


def test_session_policy_event_sink_roundtrips(tmp_path):
    """A FileAccessProxy can emit straight into a session's JSONL log."""
    from clada.session import SessionSupervisor

    sup = SessionSupervisor(["echo", "ok"], sessions_dir=tmp_path, echo=False)
    sup._open_log()
    try:
        proxy = FileAccessProxy(tmp_path, runtime=None, event_sink=sup.policy_event_sink())
        proxy._report_violation("runtime/state.json", executor_pid=None)
    finally:
        sup._close_log()

    events = [
        json.loads(ln)
        for ln in sup.log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert any(e["event"] == "policy_violation" for e in events)
