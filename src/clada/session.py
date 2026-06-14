#!/usr/bin/env python3
"""
CLADA Session Supervisor — Phase 2.

Thin runtime wrapper that launches an arbitrary agent command inside the
existing :class:`clada.orchestrator.PTYProcess`, assigns a session id, and
records a structured JSONL event log under ``runtime/sessions/``.

Design notes
------------
* This module is intentionally decoupled from the interactive REPL. It reuses
  ``PTYProcess`` for the pseudo-terminal mechanics but owns the session
  lifecycle, exit-status reaping and the JSONL event stream itself.
* JSONL contract: one JSON object per line. Every record carries ``ts``,
  ``session_id`` and ``event``; event-specific fields follow.
* PTY limitation: a pseudo-terminal merges the child's stderr into the same
  stream as stdout, so ``process_output`` events are tagged ``stream="stdout"``. True
  stream separation would require pipe-based capture and is out of scope for
  the Phase 2 smoke path.
* Sensitive data: this wrapper never reads file contents or the environment.
  Persisted JSONL command/output fields are redacted before write. Live terminal
  echo still mirrors the child process output because the caller explicitly
  chose an interactive run; LOG-03's redaction guarantee applies to CLADA's
  persisted logs.
"""

import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from clada.orchestrator import CLADA_ROOT, FileAccessProxy, PTYProcess, RuntimeState
from clada.policy import (
    EnforcementStatus,
    EventSink,
    evaluate_enforcement,
    redact_path,
    redact_text,
)

# Session logs live next to the rest of the runtime state.
DEFAULT_SESSIONS_DIR = CLADA_ROOT / "runtime" / "sessions"

# Exit code used when the requested command cannot be located/started, mirroring
# the POSIX shell "command not found" convention.
EXIT_COMMAND_NOT_FOUND = 127
# Exit code for a usage error (no command supplied).
EXIT_USAGE = 2
# Exit code used when CLADA refuses to launch a child because policy
# enforcement is degraded and no explicit owner approval was provided.
EXIT_POLICY_BLOCKED = 78


def _clada_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("clada")
        except PackageNotFoundError:
            return "unknown"
    except Exception:
        return "unknown"


def new_session_id() -> str:
    """Generate a sortable, collision-resistant session id."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"sess-{stamp}-{uuid.uuid4().hex[:8]}"


def setup_guidance(executable: str) -> str:
    """Return actionable, command-specific setup guidance for a missing exe."""
    known = {
        "claude": "Install the Claude Code CLI: npm install -g @anthropic-ai/claude-code",
        "codex": "Install the Codex CLI and ensure 'codex' is on your PATH.",
    }
    base = os.path.basename(executable)
    hint = known.get(base)
    if hint:
        return hint
    return (
        f"'{executable}' was not found. Ensure it is installed and on your PATH "
        f"(check with: which {base}), or pass an absolute path: "
        f"clada run -- /full/path/to/{base} ..."
    )


class SessionSupervisor:
    """Supervises a single ``clada run`` session end-to-end."""

    def __init__(
        self,
        command: List[str],
        runtime: Optional[RuntimeState] = None,
        sessions_dir: Optional[Path] = None,
        session_id: Optional[str] = None,
        echo: bool = True,
        enforce_policy: bool = True,
    ):
        self.command = list(command or [])
        self.runtime = runtime
        self.sessions_dir = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR
        self.session_id = session_id or new_session_id()
        # When True, child terminal output is mirrored to our stdout so the
        # user sees the agent live; tests disable this to stay quiet.
        self.echo = echo
        self.enforce_policy = enforce_policy

        self._log_fh = None
        self._start_monotonic: Optional[float] = None
        self._proc: Optional[PTYProcess] = None
        self._policy_proxy: Optional[FileAccessProxy] = None

    # ── log path ────────────────────────────────────────────────────
    @property
    def log_path(self) -> Path:
        return self.sessions_dir / f"{self.session_id}.jsonl"

    # ── JSONL emission ──────────────────────────────────────────────
    def _open_log(self):
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        # Line-buffered text handle; every record is flushed on write so a
        # crashed session still leaves a readable partial log.
        self._log_fh = self.log_path.open("a", encoding="utf-8")

    def _close_log(self):
        if self._log_fh is not None:
            try:
                self._log_fh.flush()
                self._log_fh.close()
            finally:
                self._log_fh = None

    def _emit(self, event: str, **fields):
        record = {
            "ts": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event": event,
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False)
        if self._log_fh is not None:
            self._log_fh.write(line + "\n")
            self._log_fh.flush()

    def _elapsed(self) -> float:
        if self._start_monotonic is None:
            return 0.0
        return round(time.monotonic() - self._start_monotonic, 3)

    def _redact_command(self) -> List[str]:
        """Return a log-safe copy of the command argv."""
        return [redact_text(part) for part in self.command]

    # ── policy events (LOG-03) ──────────────────────────────────────
    def policy_event_sink(self) -> EventSink:
        """Return a ``(event_name, fields)`` sink for a FileAccessProxy to emit
        ``policy_degraded`` / ``policy_violation`` events into this session's
        JSONL log."""
        def sink(event: str, fields: dict) -> None:
            self._emit(event, **fields)
        return sink

    def emit_policy_status(self, status: EnforcementStatus) -> EnforcementStatus:
        """Record the enforcement status for this session.

        Emits ``policy_degraded`` when enforcement is not fully intact (so the
        log never implies hardening that didn't happen), and ``policy_enforced``
        on the clean path. Returns the status for convenience.
        """
        event = "policy_degraded" if status.degraded else "policy_enforced"
        self._emit(event, **status.to_event())
        return status

    def emit_policy_violation(self, path, *, rule: str = "unknown",
                              action: str = "log", killed: bool = False) -> None:
        """Emit a ``policy_violation`` event with the path redacted first."""
        self._emit(
            "policy_violation",
            path=redact_path(path),
            rule=rule,
            action=action,
            killed=killed,
        )

    # ── validation ──────────────────────────────────────────────────
    def _validate_command(self) -> Optional[str]:
        if not self.command:
            return "No command provided. Usage: clada run -- <agent command>"
        exe = self.command[0]
        if shutil.which(exe) is not None:
            return None
        # Allow an explicit path to an executable file.
        if (os.sep in exe or (os.altsep and os.altsep in exe)):
            p = Path(exe)
            if p.exists() and os.access(str(p), os.X_OK):
                return None
        return f"Command not found: {exe}"

    # ── policy gate ──────────────────────────────────────────────────
    def _prepare_policy_gate(self) -> bool:
        """Apply and record the non-interactive ``clada run`` policy gate.

        ``clada run`` has no live Owner prompt, so degraded enforcement fails
        closed by default. This mirrors the interactive REPL gate without
        silently launching an agent while chmod/fswatch protection is absent.
        """
        if not self.enforce_policy:
            return True

        proxy = FileAccessProxy(
            CLADA_ROOT,
            self.runtime,
            event_sink=self.policy_event_sink(),
        )
        self._policy_proxy = proxy

        harden_status = proxy.harden_protected_paths()
        gate = proxy.assess_enforcement(harden_status)
        self.emit_policy_status(gate)

        decision = evaluate_enforcement(
            gate,
            owner_approved=False,
            fail_closed=True,
        )
        if decision.allowed:
            return True

        self._emit(
            "policy_blocked",
            reason=decision.reason,
            requires_owner_approval=decision.requires_owner_approval,
        )
        print(
            f"Policy enforcement degraded; refusing to launch: {decision.reason}",
            file=sys.stderr,
        )
        for reason in gate.degraded_reasons:
            print(f"  - {reason}", file=sys.stderr)
        return False

    # ── child process loop ──────────────────────────────────────────
    def _run_process(self) -> int:
        proc = PTYProcess(self.command, self.session_id, self.runtime)
        if not proc.start():
            self._emit(
                "error",
                message=f"Failed to start process: {self.command[0]}",
                hint=setup_guidance(self.command[0]),
            )
            return EXIT_COMMAND_NOT_FOUND
        self._proc = proc
        pid = proc.pid
        assert pid is not None  # start() returned True, so the fork succeeded
        # process change: started
        self._emit("process_start", pid=pid)
        if self._policy_proxy is not None:
            monitor_status = self._policy_proxy.start_fswatch(pid)
            if monitor_status.degraded:
                self.emit_policy_status(monitor_status)

        exit_code = 0
        try:
            while True:
                chunk = proc.drain_output()
                if chunk:
                    self._write_output(chunk)
                try:
                    wpid, status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    wpid, status = pid, 0
                if wpid != 0:
                    exit_code = os.waitstatus_to_exitcode(status)
                    break
                time.sleep(0.02)

            # Collect any trailing output the reader thread buffered after exit.
            for _ in range(10):
                chunk = proc.drain_output()
                if chunk:
                    self._write_output(chunk)
                else:
                    time.sleep(0.02)
        finally:
            # Stop the reader thread and release the PTY master fd.
            proc._running = False
            if proc.master_fd is not None:
                try:
                    os.close(proc.master_fd)
                except OSError:
                    pass

        # process change: exited
        self._emit("process_exit", pid=pid, exit_status=exit_code)
        return exit_code

    def _write_output(self, chunk: str):
        # PTY merges stderr into stdout; tag the unified stream accordingly.
        self._emit("process_output", stream="stdout", data=redact_text(chunk))
        if self.echo:
            sys.stdout.write(chunk)
            sys.stdout.flush()

    # ── public entry point ──────────────────────────────────────────
    def run(self) -> int:
        self._start_monotonic = time.monotonic()
        self._open_log()
        try:
            self._emit(
                "session_start",
                command=self._redact_command(),
                cwd=str(Path.cwd()),
                clada_version=_clada_version(),
                supervisor_pid=os.getpid(),
            )
            # Explicit command record (RUN-02): what we were asked to run.
            self._emit("command", argv=self._redact_command())

            err = self._validate_command()
            if err:
                hint = setup_guidance(self.command[0]) if self.command else (
                    "Provide a command after '--', e.g. clada run -- echo ok"
                )
                self._emit("error", message=err, hint=hint)
                print(err, file=sys.stderr)
                print(hint, file=sys.stderr)
                exit_code = EXIT_USAGE if not self.command else EXIT_COMMAND_NOT_FOUND
                self._emit(
                    "session_end",
                    exit_status=exit_code,
                    duration_seconds=self._elapsed(),
                    ok=False,
                )
                return exit_code

            if not self._prepare_policy_gate():
                self._emit(
                    "session_end",
                    exit_status=EXIT_POLICY_BLOCKED,
                    duration_seconds=self._elapsed(),
                    ok=False,
                )
                return EXIT_POLICY_BLOCKED

            exit_code = self._run_process()
            self._emit(
                "session_end",
                exit_status=exit_code,
                duration_seconds=self._elapsed(),
                ok=(exit_code == 0),
            )
            return exit_code
        except Exception as e:  # pragma: no cover - defensive
            self._emit("error", message=f"Unexpected supervisor error: {e}")
            self._emit(
                "session_end",
                exit_status=1,
                duration_seconds=self._elapsed(),
                ok=False,
            )
            return 1
        finally:
            if self._policy_proxy is not None:
                try:
                    self._policy_proxy.stop_fswatch()
                    self._policy_proxy.restore_protected_paths()
                except Exception as e:  # pragma: no cover - defensive
                    self._emit("error", message=f"Policy cleanup failed: {e}")
            self._close_log()


def run_session(
    command: List[str],
    runtime: Optional[RuntimeState] = None,
    sessions_dir: Optional[Path] = None,
    echo: bool = True,
    enforce_policy: bool = True,
) -> int:
    """Convenience wrapper: build a supervisor and run one session."""
    supervisor = SessionSupervisor(
        command,
        runtime=runtime,
        sessions_dir=sessions_dir,
        echo=echo,
        enforce_policy=enforce_policy,
    )
    return supervisor.run()
