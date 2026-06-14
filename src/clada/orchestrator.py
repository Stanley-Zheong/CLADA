#!/usr/bin/env python3
"""
CLADA Orchestrator — Core State Machine + PTY Manager
Phase 1: PTY wrapping, state machine, pattern monitor, heartbeat
Target: macOS + Claude Code CLI
"""

import os, sys, pty, signal, threading, time, json, re, subprocess, select
import termios, tty, fcntl
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

from clada.policy import (
    PathPolicy,
    EnforcementStatus,
    EventSink,
    evaluate_enforcement,
    merge_statuses,
    redact_path,
)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
CLADA_ROOT = Path(__file__).parent.parent.parent
RUNTIME_DIR = CLADA_ROOT / "runtime"
COMM_DIR = CLADA_ROOT / ".comm"
DOCS_DIR = CLADA_ROOT / "docs"
DECISIONS_DIR = DOCS_DIR / "decisions"
SPEC_DIR = DOCS_DIR / "spec"
SRC_DIR = CLADA_ROOT / "src"

STATE_FILE = RUNTIME_DIR / "current_state.json"
PROGRESS_FILE = RUNTIME_DIR / "current_progress.md"
INTERRUPTED_FILE = RUNTIME_DIR / "interrupted_state.json"
Q_WAITING_FILE = COMM_DIR / "q_waiting.json"
AUDIT_REPORT_FILE = COMM_DIR / "audit_report.json"

HEARTBEAT_INTERVAL = 30       # seconds before heartbeat probe
QUOTA_DEFAULT = 10            # ask_verifier quota per iteration
TRACE_FILE_THRESHOLD = 3      # files modified before TRACE required

# Pattern triggers (matched against Executor stdout)
PATTERNS = {
    "req_review":   re.compile(r'\[REQ_REVIEW\]', re.IGNORECASE),
    "done":         re.compile(r'\[DONE\]', re.IGNORECASE),
    "b_plan":       re.compile(r'\[B_PLAN\]', re.IGNORECASE),
    "trace":        re.compile(r'\[TRACE\]', re.IGNORECASE),
    "access_denied": re.compile(r'(EACCES|Permission denied|Access denied)', re.IGNORECASE),
}

SLASH_COMMANDS = {
    "/init", "/propose", "/execute", "/merge", "/reject", "/abort",
    "/status", "/help", "/quota", "/autopilot", "/dsl",
}

console = Console() if HAS_RICH else None


# ─────────────────────────────────────────────
# State Machine
# ─────────────────────────────────────────────
class State(str, Enum):
    IDLE             = "IDLE"
    BOOTSTRAP        = "BOOTSTRAP"
    PROPOSING        = "PROPOSING"
    EXECUTING        = "EXECUTING"
    SUSPENDED        = "SUSPENDED"
    ARBITRATING      = "ARBITRATING"
    AUDITING         = "AUDITING"
    PENDING_COMMIT   = "PENDING_COMMIT"
    WAITING_FOR_OWNER = "WAITING_FOR_OWNER"


VALID_TRANSITIONS = {
    State.IDLE:             {State.BOOTSTRAP, State.PROPOSING},
    State.BOOTSTRAP:        {State.IDLE},
    State.PROPOSING:        {State.EXECUTING, State.IDLE},
    State.EXECUTING:        {State.SUSPENDED, State.AUDITING, State.IDLE},
    State.SUSPENDED:        {State.ARBITRATING},
    State.ARBITRATING:      {State.EXECUTING},
    State.AUDITING:         {State.EXECUTING, State.PENDING_COMMIT, State.WAITING_FOR_OWNER},
    State.PENDING_COMMIT:   {State.IDLE, State.EXECUTING},
    State.WAITING_FOR_OWNER:{State.IDLE, State.EXECUTING},
}

STATE_COLORS = {
    State.IDLE:              "dim",
    State.BOOTSTRAP:         "yellow",
    State.PROPOSING:         "cyan",
    State.EXECUTING:         "green",
    State.SUSPENDED:         "yellow",
    State.ARBITRATING:       "cyan",
    State.AUDITING:          "blue",
    State.PENDING_COMMIT:    "magenta",
    State.WAITING_FOR_OWNER: "red",
}


# ─────────────────────────────────────────────
# Runtime State (persisted to current_state.json)
# ─────────────────────────────────────────────
class RuntimeState:
    def __init__(self):
        self.state: State = State.IDLE
        self.active_agent: str = "none"
        self.src_lock: bool = False
        self.executor_pid: Optional[int] = None
        self.verifier_pid: Optional[int] = None
        self.quota_remaining: int = QUOTA_DEFAULT
        self.iteration_id: str = "IT-000"
        self.last_trace_ts: float = 0.0
        self.b_plan_detected: bool = False
        self.autopilot: bool = False
        self.iteration_counter: int = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                d = json.loads(STATE_FILE.read_text())
                self.state = State(d.get("state", "IDLE"))
                self.active_agent = d.get("active_agent", "none")
                self.src_lock = d.get("src_lock", False)
                self.quota_remaining = d.get("quota_remaining", QUOTA_DEFAULT)
                self.iteration_id = d.get("iteration_id", "IT-000")
                self.last_trace_ts = d.get("last_trace_ts", 0.0)
                self.b_plan_detected = d.get("b_plan_detected", False)
                self.autopilot = d.get("autopilot", False)
                self.iteration_counter = d.get("iteration_counter", 0)
            except Exception:
                pass  # start fresh on corrupt file

    def save(self):
        with self._lock:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({
                "state":             self.state.value,
                "active_agent":      self.active_agent,
                "src_lock":          self.src_lock,
                "executor_pid":      self.executor_pid,
                "verifier_pid":      self.verifier_pid,
                "quota_remaining":   self.quota_remaining,
                "iteration_id":      self.iteration_id,
                "last_trace_ts":     self.last_trace_ts,
                "b_plan_detected":   self.b_plan_detected,
                "autopilot":         self.autopilot,
                "iteration_counter": self.iteration_counter,
                "updated_at":        datetime.now().isoformat(),
            }, indent=2))

    def transition(self, new_state: State) -> bool:
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            _log(f"[STATE] Invalid transition {self.state} → {new_state}", "red")
            return False
        old = self.state
        self.state = new_state
        self.save()
        _log(f"[STATE] {old.value} → {new_state.value}", "green")
        return True

    def next_iteration(self) -> str:
        self.iteration_counter += 1
        self.iteration_id = f"IT-{self.iteration_counter:03d}"
        self.quota_remaining = QUOTA_DEFAULT
        self.b_plan_detected = False
        self.save()
        return self.iteration_id


# ─────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────
def _log(msg: str, color: str = "white"):
    ts = datetime.now().strftime("%H:%M:%S")
    if HAS_RICH and console:
        console.print(f"[dim]{ts}[/dim] {msg}", style=color)
    else:
        print(f"{ts} {msg}")

def _banner(title: str, subtitle: str = "", color: str = "green"):
    if HAS_RICH and console:
        console.print(Panel(
            f"[bold]{title}[/bold]\n[dim]{subtitle}[/dim]" if subtitle else f"[bold]{title}[/bold]",
            border_style=color, expand=False
        ))
    else:
        print(f"\n{'='*50}\n{title}\n{'='*50}")


# ─────────────────────────────────────────────
# PTY Process Manager
# ─────────────────────────────────────────────
class PTYProcess:
    """
    Wraps a CLI agent (e.g. `claude`) in a pseudo-terminal.
    Gateway intercepts all I/O. SIGSTOP/SIGCONT for physical suspend.

    RISK-01 NOTE: On macOS, `claude` communicates with Anthropic servers
    over HTTPS long-polling. SIGSTOP beyond ~60s may cause TCP timeout.
    Gateway checks connection health before SIGCONT and re-injects context
    if needed. This is marked as a MUST-VERIFY technical assumption.
    """

    def __init__(self, cmd: list[str], name: str, runtime: Optional[RuntimeState] = None):
        self.cmd = cmd
        self.name = name
        self.runtime = runtime
        self.master_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self._output_buf: list[str] = []
        self._buf_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        try:
            self.pid, self.master_fd = pty.fork()
        except Exception as e:
            _log(f"[PTY] fork failed: {e}", "red")
            return False

        if self.pid == 0:
            # Child process — exec the agent
            os.execvp(self.cmd[0], self.cmd)
            sys.exit(1)
        else:
            # Parent — set non-blocking read on master fd
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._running = True
            self._reader_thread = threading.Thread(
                target=self._read_loop, daemon=True, name=f"reader-{self.name}"
            )
            self._reader_thread.start()
            _log(f"[PTY] {self.name} started (pid={self.pid})", "green")
            return True

    def _read_loop(self):
        """Continuously drain master_fd and buffer output."""
        while self._running:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if r:
                    data = os.read(self.master_fd, 4096)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        with self._buf_lock:
                            self._output_buf.append(text)
            except OSError:
                break

    def write(self, text: str):
        """Send text to the agent's stdin via master PTY fd."""
        if self.master_fd is None:
            return
        try:
            os.write(self.master_fd, text.encode("utf-8"))
        except OSError as e:
            _log(f"[PTY] write error ({self.name}): {e}", "red")

    def inject(self, message: str):
        """Inject a system notification as if typed by the user."""
        notification = (
            f"\n[SYSTEM_NOTIFICATION]: {message}\n"
        )
        self.write(notification)
        _log(f"[PTY] Injected to {self.name}: {message[:80]}...", "cyan")

    def drain_output(self) -> str:
        """Return and clear buffered output."""
        with self._buf_lock:
            out = "".join(self._output_buf)
            self._output_buf.clear()
        return out

    def suspend(self):
        """
        SIGSTOP the agent process.
        macOS note: PTY master_fd remains open; child just pauses.
        TCP connection health check should follow within ~10s.
        """
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGSTOP)
                _log(f"[PTY] {self.name} SUSPENDED (pid={self.pid})", "yellow")
            except ProcessLookupError:
                _log(f"[PTY] {self.name} already gone", "dim")

    def resume(self, context_snippet: Optional[str] = None):
        """
        SIGCONT the agent. If context_snippet provided, inject it first
        (RISK-01 fallback: TCP reconnection context re-sync).
        """
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGCONT)
                _log(f"[PTY] {self.name} RESUMED (pid={self.pid})", "green")
                if context_snippet:
                    time.sleep(0.5)  # brief wait for agent to wake
                    self.inject(
                        f"Context re-sync after suspension. "
                        f"Resume task per: {context_snippet[:200]}"
                    )
            except ProcessLookupError:
                _log(f"[PTY] {self.name} not found on resume", "red")

    def is_alive(self) -> bool:
        if not self.pid:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def terminate(self):
        self._running = False
        if self.pid and self.is_alive():
            try:
                os.kill(self.pid, signal.SIGTERM)
                time.sleep(0.3)
                if self.is_alive():
                    os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        _log(f"[PTY] {self.name} terminated", "dim")


# ─────────────────────────────────────────────
# Pattern Monitor
# ─────────────────────────────────────────────
class PatternMonitor:
    """
    Daemon thread that sniffs Executor stdout for trigger patterns.
    Fires callbacks on match.
    """

    def __init__(self, process: PTYProcess, runtime: RuntimeState):
        self.process = process
        self.runtime = runtime
        self._callbacks: dict[str, list] = {k: [] for k in PATTERNS}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def on(self, pattern_key: str, callback):
        self._callbacks[pattern_key].append(callback)

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="pattern-monitor"
        )
        self._thread.start()

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        accumulated = ""
        while self._running:
            chunk = self.process.drain_output()
            if chunk:
                accumulated += chunk
                # Print to Owner terminal (passthrough)
                sys.stdout.write(chunk)
                sys.stdout.flush()
                # Check patterns
                for key, pattern in PATTERNS.items():
                    if pattern.search(accumulated):
                        # Update trace timestamp
                        if key == "trace":
                            self.runtime.last_trace_ts = time.time()
                            self.runtime.save()
                        if key == "b_plan" and not self.runtime.b_plan_detected:
                            self.runtime.b_plan_detected = True
                            self.runtime.save()
                            _log("[MONITOR] [B_PLAN] detected — will block autopilot merge", "yellow")
                        for cb in self._callbacks.get(key, []):
                            try:
                                cb(accumulated)
                            except Exception as e:
                                _log(f"[MONITOR] callback error: {e}", "red")
                        # Consume matched portion to avoid re-firing
                        accumulated = accumulated[pattern.search(accumulated).end():]
            time.sleep(0.05)


# ─────────────────────────────────────────────
# Heartbeat Guardian
# ─────────────────────────────────────────────
class HeartbeatGuardian:
    """
    Fires every HEARTBEAT_INTERVAL seconds.
    If Executor has been silent, sends a harmless probe.

    RISK-03 NOTE: '#: heartbeat' is sent as a shell comment.
    Claude Code may still respond to it. The monitor thread filters
    any response to this probe before forwarding to Owner.
    This needs empirical testing to confirm zero side-effects.
    """

    def __init__(self, process: PTYProcess, runtime: RuntimeState):
        self.process = process
        self.runtime = runtime
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._guard_loop, daemon=True, name="heartbeat"
        )
        self._thread.start()

    def stop(self):
        self._running = False

    def _guard_loop(self):
        while self._running:
            time.sleep(HEARTBEAT_INTERVAL)
            if not self._running:
                break
            if self.runtime.state != State.EXECUTING:
                continue
            elapsed = time.time() - self.runtime.last_trace_ts
            if elapsed >= HEARTBEAT_INTERVAL:
                _log(f"[HEARTBEAT] {elapsed:.0f}s silent — probing executor", "dim")
                # Level 1: harmless shell comment probe
                self.process.write("#: heartbeat\n")
                time.sleep(3)
                # Level 2: if still silent, ask for status
                elapsed2 = time.time() - self.runtime.last_trace_ts
                if elapsed2 >= HEARTBEAT_INTERVAL:
                    _log("[HEARTBEAT] No response — requesting status summary", "yellow")
                    self.process.inject(
                        "Please output your current status as a [TRACE] summary: "
                        "list modified files and current task."
                    )


# ─────────────────────────────────────────────
# File Access Proxy (macOS: via wrapper approach)
# ─────────────────────────────────────────────
class FileAccessProxy:
    """
    Best-effort file-access protection around an Executor agent.

    On macOS, full LD_PRELOAD / syscall interception isn't available for
    system-integrity-protected processes, so this is **not** a sandbox. We use
    two complementary, openly-degradable mechanisms:

    1. chmod hardening: make CLADA's own guardrail directories read-only before
       the Executor starts (best-effort; may fail on some filesystems).
    2. fswatch monitoring: detect (and optionally act on) unauthorised writes
       (best-effort; absent if fswatch isn't installed).

    Both mechanisms can partially fail. Rather than pretend success, the
    enforcement methods return a :class:`~clada.policy.EnforcementStatus` and,
    when an ``event_sink`` is wired, emit structured ``policy_degraded`` /
    ``policy_violation`` events. The decision of whether to proceed when
    degraded lives in :func:`clada.policy.evaluate_enforcement`, not here.

    RISK-04 NOTE: this is a speed-bump, not a jail. Do not describe it as
    hardened sandboxing.
    """

    def __init__(
        self,
        project_root: Path,
        runtime: Optional[RuntimeState] = None,
        policy: Optional[PathPolicy] = None,
        event_sink: Optional[EventSink] = None,
    ):
        self.root = project_root
        self.runtime = runtime
        self.policy = policy or PathPolicy.default()
        # Optional callable(event_name, fields) — typically a SessionSupervisor
        # policy-event emitter. Absent in pure-CLI flows.
        self.event_sink = event_sink
        self._watcher_proc: Optional[subprocess.Popen] = None

    # ── event helper ────────────────────────────────────────────────
    def _emit(self, event: str, fields: dict) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event, fields)
        except Exception as e:  # never let logging break enforcement
            _log(f"[PROXY] event sink error: {e}", "dim")

    def _chmod(self, path: Path, mode: str) -> bool:
        """Run a recursive chmod, returning True on success. Never raises."""
        try:
            r = subprocess.run(["chmod", "-R", mode, str(path)], capture_output=True)
            return r.returncode == 0
        except Exception:
            return False

    # ── hardening (chmod) ───────────────────────────────────────────
    def harden_protected_paths(self) -> EnforcementStatus:
        """Make guardrail directories read-only; report what actually stuck.

        Returns an :class:`EnforcementStatus`. A chmod that fails (non-zero
        exit, missing tool, read-only FS) is recorded as a degraded reason
        rather than swallowed.
        """
        status = EnforcementStatus(chmod_attempted=True)
        for path_str in self.policy.harden_dirs:
            p = self.root / path_str
            if not p.exists():
                continue
            if self._chmod(p, "555"):
                status.hardened_paths.append(path_str)
            else:
                status.chmod_failures.append(path_str)
                status.add_reason(f"chmod failed on {path_str}")

        if status.hardened_paths and not status.chmod_failures:
            _log("[PROXY] Protected paths hardened (chmod 555)", "dim")
        elif status.chmod_failures:
            _log(f"[PROXY] chmod hardening degraded: {status.chmod_failures}", "yellow")
        else:
            _log("[PROXY] No guardrail paths present to harden", "dim")
        return status

    def restore_protected_paths(self) -> EnforcementStatus:
        """Restore writable permissions; report any restore failure."""
        status = EnforcementStatus()
        for path_str in self.policy.harden_dirs:
            p = self.root / path_str
            if not p.exists():
                continue
            if not self._chmod(p, "755"):
                status.restore_failures.append(path_str)
                status.add_reason(f"permission restore failed on {path_str}")
        if status.restore_failures:
            _log(f"[PROXY] permission restore degraded: {status.restore_failures}", "yellow")
        return status

    def lock_src_for_audit(self):
        """Lock src/ during AUDITING state."""
        src = self.root / "src"
        if src.exists():
            self._chmod(src, "555")
            self.runtime.src_lock = True
            self.runtime.save()
            _log("[PROXY] src/ locked for AUDITING (chmod 555)", "yellow")

    def unlock_src(self):
        src = self.root / "src"
        if src.exists():
            self._chmod(src, "755")
        self.runtime.src_lock = False
        self.runtime.save()
        _log("[PROXY] src/ unlocked", "green")

    # ── monitoring (fswatch) ────────────────────────────────────────
    def fswatch_available(self) -> bool:
        """True if the fswatch binary is on PATH. Never raises."""
        try:
            return subprocess.run(
                ["which", "fswatch"], capture_output=True
            ).returncode == 0
        except Exception:
            return False

    def assess_enforcement(self, harden_status: EnforcementStatus) -> EnforcementStatus:
        """Combine an applied chmod status with an fswatch *availability* probe
        into the status used to gate execution — without starting fswatch yet
        (it needs the executor pid). Used by the fail-closed gate (POL-04)."""
        probe = EnforcementStatus(fswatch_attempted=True)
        probe.fswatch_available = self.fswatch_available()
        if not probe.fswatch_available:
            probe.add_reason("fswatch not installed — write monitoring unavailable")
        return merge_statuses(harden_status, probe)

    def start_fswatch(self, executor_pid: Optional[int] = None) -> EnforcementStatus:
        """
        Start fswatch on protected directories.
        On unauthorized write: log + optionally kill the writing process.
        RISK-02: bind-mount fswatch behavior needs empirical verification.

        Returns an :class:`EnforcementStatus` describing whether monitoring is
        actually live. Missing fswatch is a degraded reason, not a silent skip.
        """
        status = EnforcementStatus(fswatch_attempted=True)
        watch_paths = [str(self.root / d) for d in self.policy.watch_dirs]
        existing = [p for p in watch_paths if Path(p).exists()]
        if not existing:
            status.add_reason("no watch directories present")
            return status

        # Check fswatch availability
        available = self.fswatch_available()
        status.fswatch_available = available
        if not available:
            status.add_reason("fswatch not installed — write monitoring disabled")
            _log("[PROXY] fswatch not found — install with: brew install fswatch", "yellow")
            _log("[PROXY] File write monitoring disabled (chmod-only protection)", "dim")
            return status

        try:
            self._watcher_proc = subprocess.Popen(
                ["fswatch", "-r", "--event=Updated", "--event=Created"] + existing,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            t = threading.Thread(
                target=self._fswatch_loop,
                args=(executor_pid,),
                daemon=True, name="fswatch"
            )
            t.start()
            status.fswatch_active = True
            _log("[PROXY] fswatch monitoring started", "dim")
        except Exception as e:
            status.add_reason(f"fswatch start failed: {e}")
            _log(f"[PROXY] fswatch start failed: {e}", "yellow")
        return status

    def _fswatch_loop(self, executor_pid: Optional[int]):
        if not self._watcher_proc or self._watcher_proc.stdout is None:
            return
        for line in self._watcher_proc.stdout:
            path = line.decode(errors="replace").strip()
            self._report_violation(path, executor_pid)

    def _report_violation(self, path: str, executor_pid: Optional[int]) -> None:
        """Handle a detected unauthorised write: log (redacted), emit a
        ``policy_violation`` event, and optionally kill the writer.

        Extracted from the watch loop so it is unit-testable without an actual
        fswatch process or a real kill."""
        safe = redact_path(path, self.policy)
        _log(f"[SECURITY] ⚠️  Unauthorized write detected: {safe}", "red")
        killed = False
        if executor_pid:
            _log(f"[SECURITY] Killing executor pid={executor_pid}", "red")
            try:
                os.kill(executor_pid, signal.SIGKILL)
                killed = True
            except ProcessLookupError:
                pass
        self._emit("policy_violation", {
            "path": safe,
            "rule": self.policy.classify_write(path),
            "action": "kill" if killed else "log",
            "killed": killed,
        })

    def stop_fswatch(self):
        if self._watcher_proc:
            self._watcher_proc.terminate()


# ─────────────────────────────────────────────
# Clean Shutdown Protocol
# ─────────────────────────────────────────────
def clean_shutdown(runtime: RuntimeState, reason: str = "Quota exhausted"):
    """
    Atomic shutdown sequence:
    1. git commit snapshot to interrupted branch
    2. Save interrupted_state.json
    3. Prompt Owner for recovery choice
    """
    _banner("Clean Shutdown Protocol", reason, "yellow")

    iteration_id = runtime.iteration_id

    # 1. Git snapshot
    branch = f"clada/interrupted/{iteration_id}"
    cmds = [
        ["git", "add", "-A"],
        ["git", "commit", "-m",
         f"[CLADA_INTERRUPTED]: {reason} at {iteration_id}", "--no-verify"],
        ["git", "checkout", "-b", branch],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, cwd=str(CLADA_ROOT))
        if r.returncode != 0 and b"nothing to commit" not in r.stderr:
            _log(f"[SHUTDOWN] git cmd failed: {' '.join(cmd)}: {r.stderr.decode()[:100]}", "red")

    # 2. Save interrupted state
    last_trace = ""
    if PROGRESS_FILE.exists():
        last_trace = PROGRESS_FILE.read_text()[-500:]

    pending_q = {}
    if Q_WAITING_FILE.exists():
        try:
            pending_q = json.loads(Q_WAITING_FILE.read_text())
        except Exception:
            pass

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    INTERRUPTED_FILE.write_text(json.dumps({
        "iteration_id":    iteration_id,
        "reason":          reason,
        "timestamp":       datetime.now().isoformat(),
        "last_trace":      last_trace,
        "pending_question": pending_q,
        "branch":          branch,
    }, indent=2, ensure_ascii=False))

    _log(f"[SHUTDOWN] State saved → {INTERRUPTED_FILE}", "green")

    # 3. Owner prompt
    print(f"\n{'─'*60}")
    print(f"任务在 {iteration_id} 中断。代码已快照至分支: {branch}")
    print(f"{'─'*60}")
    print("请选择恢复策略:")
    print("  [A] 补充 Quota 继续执行")
    print("  [B] 回滚至 main 分支")
    print("  [C] 保留分支，稍后处理")
    choice = input("\n请输入选择 [A/B/C]: ").strip().upper()

    if choice == "A":
        runtime.quota_remaining = QUOTA_DEFAULT
        runtime.save()
        _log(f"[SHUTDOWN] Quota 已补充至 {QUOTA_DEFAULT}", "green")
        return "continue"
    elif choice == "B":
        subprocess.run(["git", "checkout", "main"], cwd=str(CLADA_ROOT))
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=str(CLADA_ROOT))
        _log("[SHUTDOWN] 已回滚至 main", "yellow")
        runtime.transition(State.IDLE)
        return "rollback"
    else:
        runtime.transition(State.IDLE)
        _log(f"[SHUTDOWN] 分支 {branch} 保留，系统回到 IDLE", "dim")
        return "idle"


# ─────────────────────────────────────────────
# ARBITRATING: Inject Verifier Decision
# ─────────────────────────────────────────────
def inject_verifier_decision(
    executor: PTYProcess,
    decision: str,
    dr_ref: str = "",
    runtime: Optional[RuntimeState] = None
):
    """
    Append Injection: Verifier's decision is written to Executor's STDIN
    disguised as an Owner system notification.
    """
    dr_line = f"\nReferenced ADR: {dr_ref}" if dr_ref else ""
    message = (
        f"Verifier has resolved your query.{dr_line}\n"
        f"Decision: {decision}\n"
        f"Constraint: This decision is binding. Do not deviate.\n"
        f"Please integrate this decision and continue your current task."
    )
    executor.inject(message)
    if runtime:
        runtime.quota_remaining = max(0, runtime.quota_remaining - 1)
        runtime.save()
        _log(f"[QUOTA] Remaining: {runtime.quota_remaining}/{QUOTA_DEFAULT}", "cyan")


# ─────────────────────────────────────────────
# Status Display
# ─────────────────────────────────────────────
def show_status(runtime: RuntimeState):
    if not HAS_RICH:
        print(json.dumps({
            "state": runtime.state.value,
            "iteration": runtime.iteration_id,
            "quota": runtime.quota_remaining,
            "b_plan": runtime.b_plan_detected,
            "autopilot": runtime.autopilot,
        }, indent=2))
        return

    color = STATE_COLORS.get(runtime.state, "white")
    t = Table(box=box.ROUNDED, border_style="dim", show_header=False)
    t.add_column("Key", style="dim", width=20)
    t.add_column("Value")
    t.add_row("State", f"[{color} bold]{runtime.state.value}[/{color} bold]")
    t.add_row("Active Agent", runtime.active_agent)
    t.add_row("Iteration", runtime.iteration_id)
    t.add_row("Quota",
              f"[green]{runtime.quota_remaining}[/green]/{QUOTA_DEFAULT}"
              if runtime.quota_remaining > 3
              else f"[red]{runtime.quota_remaining}[/red]/{QUOTA_DEFAULT}")
    t.add_row("B_PLAN", "[red]YES[/red]" if runtime.b_plan_detected else "[green]NO[/green]")
    t.add_row("Src Lock", "[yellow]LOCKED[/yellow]" if runtime.src_lock else "unlocked")
    t.add_row("Autopilot", "[yellow]ON[/yellow]" if runtime.autopilot else "off")
    console.print(Panel(t, title="[bold]CLADA Status[/bold]", border_style=color))


# ─────────────────────────────────────────────
# REPL — Owner Command Interface
# ─────────────────────────────────────────────
class REPL:
    """
    Owner's control console. Reads slash commands and dispatches
    to the appropriate state machine transition.
    """

    def __init__(self, runtime: RuntimeState, proxy: FileAccessProxy):
        self.runtime = runtime
        self.proxy = proxy
        self.executor: Optional[PTYProcess] = None
        self.verifier: Optional[PTYProcess] = None
        self.monitor: Optional[PatternMonitor] = None
        self.heartbeat: Optional[HeartbeatGuardian] = None
        self._setup_callbacks()

    def _setup_callbacks(self):
        # These are wired once executor is started
        pass

    def _wire_executor(self, executor: PTYProcess):
        self.executor = executor
        self.monitor = PatternMonitor(executor, self.runtime)
        self.heartbeat = HeartbeatGuardian(executor, self.runtime)

        self.monitor.on("req_review",    self._on_req_review)
        self.monitor.on("done",          self._on_done)
        self.monitor.on("access_denied", self._on_access_denied)
        self.monitor.on("b_plan",        lambda _: None)  # state already updated in monitor

        self.monitor.start()
        self.heartbeat.start()

    def _on_req_review(self, ctx: str):
        if self.runtime.state != State.EXECUTING:
            return
        _log("\n[TRIGGER] [REQ_REVIEW] detected — suspending Executor", "yellow")
        if self.runtime.quota_remaining <= 0:
            _log("[QUOTA] Quota exhausted — entering Final Choice Mode", "red")
            self.executor.inject(
                "Your /ask_verifier quota is exhausted. You must make a final decision "
                "based on existing information and mark it with [B_PLAN]."
            )
            return

        if self.executor:
            self.executor.suspend()
        self.runtime.transition(State.SUSPENDED)

        # Load question from q_waiting if available
        question = ctx[-300:]
        if Q_WAITING_FILE.exists():
            try:
                q_data = json.loads(Q_WAITING_FILE.read_text())
                question = q_data.get("context", question)
            except Exception:
                pass

        _log("[ARBITRATING] Switching to Verifier…", "cyan")
        self.runtime.active_agent = "verifier"
        self.runtime.transition(State.ARBITRATING)
        self.runtime.save()

        # In a full implementation, Verifier would be a separate PTY process.
        # For Phase 1, we use a simple terminal prompt for Owner to relay.
        print(f"\n{'─'*60}")
        print(f"[ARBITRATING] Executor question:")
        print(f"  {question[-200:]}")
        print(f"{'─'*60}")
        decision = input("Enter Verifier decision (or DR reference): ").strip()
        dr_ref = input("Referenced ADR (e.g. DR-024, or blank): ").strip()

        if decision:
            self.runtime.transition(State.EXECUTING)
            self.runtime.active_agent = "executor"
            self.runtime.save()
            inject_verifier_decision(self.executor, decision, dr_ref, self.runtime)
            self.executor.resume()
        else:
            _log("[ARBITRATING] No decision provided — executor remains suspended", "red")

    def _on_done(self, ctx: str):
        if self.runtime.state != State.EXECUTING:
            return
        _log("\n[TRIGGER] [DONE] detected — initiating AUDIT", "blue")
        self._enter_auditing()

    def _on_access_denied(self, ctx: str):
        if self.runtime.state == State.EXECUTING:
            _log("[TRIGGER] ACCESS_DENIED detected — suspending for review", "red")
            if self.executor:
                self.executor.suspend()
            self.runtime.transition(State.SUSPENDED)

    def _enter_auditing(self):
        self.runtime.transition(State.AUDITING)
        self.proxy.lock_src_for_audit()
        _log("[AUDIT] src/ locked — running test suite", "blue")

        # In Phase 1: run local test script if available
        test_script = CLADA_ROOT / "test_runner.py"
        result = {"passed": True, "failure_count": 0, "b_plan": self.runtime.b_plan_detected}

        if test_script.exists():
            r = subprocess.run(
                ["python3", str(test_script)],
                capture_output=True, cwd=str(CLADA_ROOT)
            )
            result["passed"] = r.returncode == 0
            result["failure_count"] = 0 if r.returncode == 0 else 1
            result["stdout"] = r.stdout.decode()[-500:]
            result["stderr"] = r.stderr.decode()[-200:]
        else:
            _log("[AUDIT] No test_runner.py found — manual audit mode", "yellow")
            ans = input("Manual audit: did tests pass? [y/N]: ").strip().lower()
            result["passed"] = ans == "y"
            result["failure_count"] = 0 if result["passed"] else 1

        # Write audit report
        COMM_DIR.mkdir(parents=True, exist_ok=True)
        AUDIT_REPORT_FILE.write_text(json.dumps({
            **result,
            "iteration_id": self.runtime.iteration_id,
            "timestamp": datetime.now().isoformat(),
        }, indent=2))

        self.proxy.unlock_src()

        if not result["passed"]:
            _log(f"[AUDIT] FAIL ({result['failure_count']} failures) — returning to Executor", "red")
            self.runtime.transition(State.EXECUTING)
            if self.executor:
                self.executor.resume(
                    f"Audit failed. Fix issues and re-output [DONE]. "
                    f"Details: {result.get('stderr', '')[:200]}"
                )
        elif result["b_plan"]:
            _log("[AUDIT] PASS but [B_PLAN] detected — WAITING_FOR_OWNER", "yellow")
            self.runtime.transition(State.WAITING_FOR_OWNER)
            _banner(
                "⚠️  Human Review Required",
                "Tests passed but [B_PLAN] decisions exist. Review audit_report.json before merging.",
                "yellow"
            )
        else:
            _log("[AUDIT] ✅ PASS — moving to PENDING_COMMIT", "green")
            self.runtime.transition(State.PENDING_COMMIT)
            it = self.runtime.iteration_id
            subprocess.run(
                ["git", "checkout", "-b", f"feature/{it}"],
                cwd=str(CLADA_ROOT), capture_output=True
            )
            _log(f"[AUDIT] Branch feature/{it} ready for /merge", "green")

    def run(self):
        _banner("CLADA Gateway", "Phase 1 · PTY + State Machine + Validator", "green")
        show_status(self.runtime)

        print("\nCommands: /init  /propose  /execute  /merge  /reject  /abort  /status  /help\n")

        while True:
            try:
                raw = input("clada> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[Ctrl+C] Use /abort to safely shut down.")
                continue

            if not raw:
                continue

            cmd = raw.split()[0].lower()
            args = raw[len(cmd):].strip()

            if cmd == "/help":
                self._cmd_help()
            elif cmd == "/status":
                show_status(self.runtime)
            elif cmd == "/init":
                self._cmd_init()
            elif cmd == "/propose":
                self._cmd_propose(args)
            elif cmd == "/execute":
                self._cmd_execute()
            elif cmd == "/merge":
                self._cmd_merge()
            elif cmd == "/reject":
                self._cmd_reject(args)
            elif cmd == "/abort":
                self._cmd_abort()
                break
            elif cmd == "/quota":
                self._cmd_quota(args)
            elif cmd == "/autopilot":
                self._cmd_autopilot(args)
            elif cmd == "/dsl":
                self._cmd_dsl(args)
            else:
                _log(f"Unknown command: {cmd}. Type /help for list.", "red")

    def _cmd_help(self):
        help_text = """
  /init              Start Bootstrap (create first Contract + DR-001)
  /propose [text]    Enter PROPOSING: Verifier refines Spec
  /execute           Start Executor on current_spec.md
  /merge             Merge feature branch (PENDING_COMMIT only)
  /reject [reason]   Reject audit, return to EXECUTING
  /abort             Clean Shutdown and exit
  /status            Show current state
  /quota [n]         Set ask_verifier quota (default: 10)
  /autopilot [on|off] Toggle Owner-offline mode
  /dsl compile <file> Compile .dsl file → contract.json + spec.md
"""
        print(help_text)

    def _cmd_init(self):
        if self.runtime.state != State.IDLE:
            _log(f"[INIT] Must be IDLE (current: {self.runtime.state})", "red")
            return
        from clada.bootstrap import run_bootstrap
        run_bootstrap(self.runtime, self.proxy)

    def _cmd_propose(self, args: str):
        if self.runtime.state != State.IDLE:
            _log(f"[PROPOSE] Must be IDLE (current: {self.runtime.state})", "red")
            return
        self.runtime.transition(State.PROPOSING)
        it = self.runtime.next_iteration()
        _log(f"[PROPOSE] Starting {it}", "cyan")

        spec_file = SPEC_DIR / "current_spec.md"
        if args:
            SPEC_DIR.mkdir(parents=True, exist_ok=True)
            spec_file.write_text(f"# Spec — {it}\n\n{args}\n")
            _log(f"[PROPOSE] Spec written to {spec_file}", "cyan")
        else:
            _log("[PROPOSE] Edit docs/spec/current_spec.md then run /execute", "dim")

    def _gate_enforcement(self, harden_status: EnforcementStatus) -> bool:
        """Return True if execution may proceed under the current enforcement.

        Combines the applied chmod result with an fswatch availability probe,
        emits a policy event, then applies the fail-closed / owner-approval
        decision (POL-04). When degraded and the Owner is offline (autopilot),
        we fail closed; when interactive, we ask for explicit approval.
        """
        gate = self.proxy.assess_enforcement(harden_status)
        # Surface what's actually enforced (LOG-03) via the proxy's event sink.
        event = "policy_degraded" if gate.degraded else "policy_enforced"
        self.proxy._emit(event, gate.to_event())

        decision = evaluate_enforcement(gate, owner_approved=False, fail_closed=True)
        if decision.allowed:
            return True

        _log(f"[POLICY] Enforcement degraded — {decision.reason}", "red")
        for r in gate.degraded_reasons:
            _log(f"[POLICY]   • {r}", "yellow")

        if self.runtime.autopilot:
            _log("[POLICY] Autopilot (Owner offline): failing closed, refusing to launch.", "red")
            self.proxy._emit("policy_blocked", {"reason": decision.reason})
            return False

        approved = self._owner_confirm(
            "Enforcement is degraded. Launch Executor anyway with reduced protection?"
        )
        re_decision = evaluate_enforcement(gate, owner_approved=approved, fail_closed=True)
        if re_decision.allowed:
            _log("[POLICY] Owner approved degraded enforcement — proceeding.", "yellow")
            self.proxy._emit("policy_override", {"reason": re_decision.reason})
            return True
        _log("[POLICY] Owner declined — failing closed.", "red")
        self.proxy._emit("policy_blocked", {"reason": decision.reason})
        return False

    def _owner_confirm(self, prompt: str) -> bool:
        """Ask the Owner a yes/no question. Defaults to 'no' (fail closed)."""
        try:
            if HAS_RICH and console:
                return bool(Confirm.ask(prompt, default=False))
            return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def _cmd_execute(self):
        if self.runtime.state not in (State.PROPOSING, State.IDLE):
            _log(f"[EXECUTE] Must be PROPOSING or IDLE (current: {self.runtime.state})", "red")
            return
        spec_file = SPEC_DIR / "current_spec.md"
        if not spec_file.exists():
            _log("[EXECUTE] No current_spec.md found — run /propose first", "red")
            return

        self.runtime.transition(State.EXECUTING)
        self.runtime.active_agent = "executor"
        self.runtime.save()

        # Apply protection and gate on it (POL-04). If enforcement is degraded
        # we fail closed unless the Owner explicitly approves — we never launch
        # the Executor while silently pretending paths are protected.
        harden_status = self.proxy.harden_protected_paths()
        if not self._gate_enforcement(harden_status):
            self.proxy.restore_protected_paths()
            self.runtime.active_agent = "none"
            self.runtime.transition(State.IDLE)
            return

        # Build claude command with spec context
        spec_content = spec_file.read_text()
        claude_cmd = self._build_claude_cmd(spec_content)

        _log(f"[EXECUTE] Launching: {' '.join(claude_cmd[:3])} …", "green")
        executor = PTYProcess(claude_cmd, "executor", self.runtime)

        if not executor.start():
            _log("[EXECUTE] Failed to start Executor", "red")
            self.runtime.transition(State.IDLE)
            return

        self.runtime.executor_pid = executor.pid
        self.runtime.save()
        self._wire_executor(executor)
        self.proxy.start_fswatch(executor.pid)

        _log(f"[EXECUTE] Executor live (pid={executor.pid}). Press Ctrl+C to interrupt.", "green")

        # Forward user typing to executor PTY
        try:
            self._passthrough_loop(executor)
        except KeyboardInterrupt:
            _log("\n[EXECUTE] Interrupted by Owner", "yellow")

        # Check quota on exit
        if self.runtime.quota_remaining <= 0 and self.runtime.state == State.EXECUTING:
            result = clean_shutdown(self.runtime, "Quota exhausted")
            if result == "continue":
                self._cmd_execute()  # restart with fresh quota

    def _build_claude_cmd(self, spec_content: str) -> list[str]:
        """
        Build the executor CLI invocation from config.
        Falls back to default `claude` CLI if no config found.
        """
        from clada.config import CLADAConfig

        config = CLADAConfig.load()
        role = config.get_executor()

        prompt_file = RUNTIME_DIR / "executor_prompt.md"
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        system_prompt = (
            "You are the CLADA Executor. Your rules:\n"
            "1. Implement ONLY what is specified. No improvisation.\n"
            "2. Output [TRACE] after every 3 file modifications.\n"
            "3. Output [REQ_REVIEW] + q_waiting.json when spec is ambiguous.\n"
            "4. Output [DONE] when implementation is complete.\n"
            "5. If quota exhausted, mark decisions with [B_PLAN].\n\n"
            f"--- CURRENT SPEC ---\n{spec_content}\n"
        )
        prompt_file.write_text(system_prompt)

        cli_path = role.cli_path or "claude"

        if role.mode == "api":
            _log(f"[CONFIG] Executor: {role.provider}/{role.model} (API mode)", "dim")
        else:
            _log(f"[CONFIG] Executor: {role.provider}/{role.model} via {cli_path}", "dim")

        return [cli_path, "--dangerously-skip-permissions", str(prompt_file)]

    def _passthrough_loop(self, executor: PTYProcess):
        """Forward Owner keyboard input to executor PTY."""
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setraw(sys.stdin.fileno())
            while executor.is_alive() and self.runtime.state == State.EXECUTING:
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if r:
                    data = os.read(sys.stdin.fileno(), 1024)
                    if data:
                        os.write(executor.master_fd, data)
        except Exception:
            pass
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    def _cmd_merge(self):
        if self.runtime.state not in (State.PENDING_COMMIT,):
            _log(f"[MERGE] Must be PENDING_COMMIT (current: {self.runtime.state})", "red")
            return
        it = self.runtime.iteration_id
        branch = f"feature/{it}"
        r = subprocess.run(
            ["git", "merge", branch, "--no-ff", "-m", f"[CLADA] Merge {it}"],
            cwd=str(CLADA_ROOT), capture_output=True
        )
        if r.returncode == 0:
            _log(f"[MERGE] ✅ {branch} merged to main", "green")
            self._archive_iteration(it)
            self.runtime.transition(State.IDLE)
        else:
            _log(f"[MERGE] ❌ git merge failed: {r.stderr.decode()[:200]}", "red")

    def _cmd_reject(self, reason: str):
        if self.runtime.state not in (State.PENDING_COMMIT, State.WAITING_FOR_OWNER):
            _log(f"[REJECT] Must be PENDING_COMMIT or WAITING_FOR_OWNER", "red")
            return
        self.runtime.transition(State.EXECUTING)
        if self.executor:
            self.executor.inject(
                f"Owner rejected the implementation. Reason: {reason or 'Not specified'}. "
                f"Please revise and re-output [DONE]."
            )
            self.executor.resume()

    def _cmd_abort(self):
        _log("[ABORT] Initiating clean shutdown…", "yellow")
        if self.executor:
            self.executor.terminate()
        if self.verifier:
            self.verifier.terminate()
        if self.monitor:
            self.monitor.stop()
        if self.heartbeat:
            self.heartbeat.stop()
        self.proxy.stop_fswatch()
        self.proxy.restore_protected_paths()
        if self.runtime.src_lock:
            self.proxy.unlock_src()
        clean_shutdown(self.runtime, "Owner /abort")
        _log("[ABORT] Done. Goodbye.", "dim")

    def _cmd_quota(self, args: str):
        try:
            n = int(args)
            self.runtime.quota_remaining = n
            self.runtime.save()
            _log(f"[QUOTA] Set to {n}", "green")
        except ValueError:
            _log(f"[QUOTA] Current: {self.runtime.quota_remaining}/{QUOTA_DEFAULT}", "cyan")

    def _cmd_autopilot(self, args: str):
        if args.lower() == "on":
            self.runtime.autopilot = True
        elif args.lower() == "off":
            self.runtime.autopilot = False
        else:
            self.runtime.autopilot = not self.runtime.autopilot
        self.runtime.save()
        status = "ON" if self.runtime.autopilot else "OFF"
        _log(f"[AUTOPILOT] {status}", "yellow" if self.runtime.autopilot else "dim")

    def _cmd_dsl(self, args: str):
        from clada.dsl.compiler import DSLCompiler
        from clada.dsl.registry import list_domains, DSLRegistry

        parts = args.split()
        sub = parts[0] if parts else ""

        if sub == "compile" and len(parts) > 1:
            dsl_path = Path(parts[1])
            if not dsl_path.exists():
                _log(f"[DSL] File not found: {dsl_path}", "red")
                return
            compiler = DSLCompiler()
            result = compiler.compile_file(dsl_path)
            if result.success:
                contract_path, spec_path = result.write_to(SPEC_DIR, DECISIONS_DIR)
                _log(f"[DSL] ✅ Compiled ({result.domain}): contract.json + spec.md", "green")
                for w in result.warnings:
                    _log(f"[DSL] ⚠️  {w}", "yellow")
            else:
                for e in result.errors:
                    _log(f"[DSL] ❌ {e}", "red")
        elif sub == "domains":
            for d in list_domains():
                info = DSLRegistry.get(d)
                desc = info.get("description", "") if info else ""
                _log(f"[DSL] {d:20s} — {desc}", "dim")
        elif sub == "template" and len(parts) > 1:
            domain = DSLRegistry.get(parts[1])
            if domain:
                for phase, info in domain.get("phases", {}).items():
                    print(f"\n;; Phase: {phase}")
                    print(info.get("template", "").strip())
                    print()
            else:
                _log(f"[DSL] Unknown domain: {parts[1]}", "red")
        else:
            _log("[DSL] Usage: /dsl compile <file>|domains|template <domain>", "yellow")

    def _archive_iteration(self, it: str):
        """Archive iteration snapshot to docs/iterations/."""
        archive = DOCS_DIR / "iterations" / f"{it}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        content = [f"# {it} — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
        if AUDIT_REPORT_FILE.exists():
            content.append(f"\n## Audit Report\n```json\n{AUDIT_REPORT_FILE.read_text()}\n```\n")
        if PROGRESS_FILE.exists():
            content.append(f"\n## Progress Log\n{PROGRESS_FILE.read_text()}\n")
        archive.write_text("".join(content))
        _log(f"[ARCHIVE] {it} → {archive}", "dim")


# ─────────────────────────────────────────────
# Policy event sink (LOG-03)
# ─────────────────────────────────────────────
def make_policy_event_logger(log_path: Path) -> EventSink:
    """Return an EventSink that appends policy events as JSONL.

    Reuses the Phase 2 JSONL contract (``ts`` + ``event`` + fields) so policy
    events from the interactive REPL are auditable alongside session logs.
    Failures to write are swallowed — logging must never break enforcement.
    """
    def sink(event: str, fields: dict) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {"ts": datetime.now().isoformat(), "event": event}
            record.update(fields or {})
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return sink


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
def main():
    for d in [RUNTIME_DIR, COMM_DIR, DOCS_DIR, DECISIONS_DIR, SPEC_DIR, SRC_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    runtime = RuntimeState()
    sink = make_policy_event_logger(RUNTIME_DIR / "sessions" / "policy-events.jsonl")
    proxy = FileAccessProxy(CLADA_ROOT, runtime, event_sink=sink)
    repl = REPL(runtime, proxy)
    repl.run()


if __name__ == "__main__":
    main()
