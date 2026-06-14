#!/usr/bin/env python3
"""
CLADA Policy Engine — Phase 3.

An explicit, testable model of *which paths CLADA tries to protect*, *how it
tries to protect them*, and — crucially — *what to do when that protection is
only partially available*.

Why this module exists
----------------------
Phase 1/2 wired path protection straight into :class:`FileAccessProxy` as a
pair of best-effort ``chmod``/``fswatch`` calls whose return codes were
ignored. That is dishonest: on a machine without ``fswatch``, or where a
``chmod`` silently fails, CLADA *looked* hardened while enforcing nothing.

This module makes the policy data and the degraded-mode contract first-class:

* :class:`PathPolicy` — the declarative set of protected/forbidden/allowed
  paths, with documented defaults (POL-01..POL-03).
* :class:`EnforcementStatus` — a structured, serialisable record of what
  enforcement actually achieved this run, including every reason it is
  degraded (POL-04, LOG-03).
* :func:`evaluate_enforcement` — the fail-closed / owner-approval decision
  applied when enforcement is degraded (POL-04).
* :func:`redact_path` / :func:`redact_text` — omission-first redaction helpers
  for sensitive paths and secret-like strings (POL-05, LOG-03).

Honesty constraint
------------------
Nothing here claims hardened sandboxing. ``chmod`` + ``fswatch`` on macOS is a
speed-bump, not a syscall jail; the language throughout ("best-effort",
"degraded", "monitoring") reflects that. See the CEO review for the rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from typing import Callable, Iterable, List, Optional


# ─────────────────────────────────────────────
# Policy model (POL-01, POL-02, POL-03)
# ─────────────────────────────────────────────
@dataclass(frozen=True)
class PathPolicy:
    """Declarative protected/forbidden/allowed path policy.

    All matching is performed on paths *relative to the project root*. The
    defaults below are deliberately conservative and documented inline so a
    reviewer can audit exactly what CLADA tries to protect.
    """

    #: Glob patterns for files whose *contents* are secret and must never be
    #: read into a log or echoed in clear (POL-01). Matched against the full
    #: relative path and every individual path component.
    protected_read_patterns: tuple = (
        ".env", ".env.*", "*.env",
        "secrets", "secrets/*", ".secrets", ".secrets/*",
        "*.pem", "*.key", "*.p12", "*.pfx", "*.crt",
        "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
        ".ssh", ".ssh/*", ".aws", ".aws/*",
        "credentials", "*.credentials", ".netrc",
    )

    #: Relative path prefixes the Executor agent must not write to (POL-02).
    #: These hold CLADA's own decision record, contract and runtime state —
    #: letting the agent rewrite them would let it edit its own guardrails.
    write_forbidden_prefixes: tuple = (
        "docs/decisions/",
        "docs/spec/contract.json",
        ".clada/",
        "runtime/",
        ".comm/",
        ".git/",
    )

    #: Relative path prefixes the Executor *is* expected to write to (POL-03).
    #: Documented so "allowed" is explicit rather than "everything not
    #: forbidden". A write outside both sets is *unknown*, not auto-allowed.
    write_allowed_prefixes: tuple = (
        "src/",
        "tests/",
        "docs/spec/current_spec.md",
        "docs/iterations/",
    )

    #: Directories we attempt to make read-only via chmod before the agent
    #: runs. Subset of the forbidden set that is safe to lock without breaking
    #: CLADA's own bookkeeping mid-run.
    harden_dirs: tuple = (
        "docs/decisions",
        "runtime",
        ".comm",
    )

    #: Directories we point fswatch at to detect unauthorised writes.
    watch_dirs: tuple = (
        "docs/decisions",
        "runtime",
    )

    @classmethod
    def default(cls) -> "PathPolicy":
        """Return the documented default policy."""
        return cls()

    # ── matching helpers ────────────────────────────────────────────
    def _relative(self, path) -> Optional[str]:
        """Best-effort POSIX relative string for matching.

        Accepts absolute or relative paths/str. Never raises — an unmatchable
        path returns ``None`` and is treated as *unknown* by callers.
        """
        if path is None:
            return None
        s = str(path).replace("\\", "/")
        # Strip an explicit leading "./" without eating a leading dot from a
        # dotfile like ".env" (which lstrip("./") would wrongly do).
        while s.startswith("./"):
            s = s[2:]
        return s or None

    def is_protected_read(self, path) -> bool:
        """True if reading/logging this path's contents would leak secrets."""
        rel = self._relative(path)
        if rel is None:
            return False
        parts = rel.split("/")
        for pattern in self.protected_read_patterns:
            if "/" in pattern:
                if fnmatch(rel, pattern) or fnmatch(rel, "*/" + pattern):
                    return True
            else:
                if any(fnmatch(part, pattern) for part in parts):
                    return True
        return False

    def is_write_forbidden(self, path) -> bool:
        """True if the Executor must not write to this path (POL-02)."""
        rel = self._relative(path)
        if rel is None:
            return False
        return any(
            rel == p.rstrip("/") or rel.startswith(p)
            for p in self.write_forbidden_prefixes
        )

    def is_write_allowed(self, path) -> bool:
        """True if writing here is explicitly sanctioned (POL-03).

        Forbidden always wins over allowed.
        """
        if self.is_write_forbidden(path):
            return False
        rel = self._relative(path)
        if rel is None:
            return False
        return any(
            rel == p.rstrip("/") or rel.startswith(p)
            for p in self.write_allowed_prefixes
        )

    def classify_write(self, path) -> str:
        """Return ``"forbidden"``, ``"allowed"`` or ``"unknown"`` for a write."""
        if self.is_write_forbidden(path):
            return "forbidden"
        if self.is_write_allowed(path):
            return "allowed"
        return "unknown"


# ─────────────────────────────────────────────
# Enforcement status (POL-04, LOG-03)
# ─────────────────────────────────────────────
class EnforcementMode(str, Enum):
    """How much of the policy is actually being enforced this run."""

    ENFORCED = "enforced"   # chmod applied AND fswatch monitoring live
    DEGRADED = "degraded"   # some protection active, some failed/unavailable
    DISABLED = "disabled"   # nothing could be enforced at all


@dataclass
class EnforcementStatus:
    """Structured outcome of an enforcement attempt — safe to serialise to a
    session event. Every field is plain JSON-friendly data."""

    chmod_attempted: bool = False
    hardened_paths: List[str] = field(default_factory=list)
    chmod_failures: List[str] = field(default_factory=list)
    restore_failures: List[str] = field(default_factory=list)
    fswatch_attempted: bool = False
    fswatch_available: bool = False
    fswatch_active: bool = False
    degraded_reasons: List[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        if reason and reason not in self.degraded_reasons:
            self.degraded_reasons.append(reason)

    @property
    def mode(self) -> EnforcementMode:
        # Fully disabled: we tried to enforce something and nothing stuck.
        attempted = self.chmod_attempted or self.fswatch_attempted
        chmod_ok = self.chmod_attempted and not self.chmod_failures and self.hardened_paths
        nothing_active = not chmod_ok and not self.fswatch_active
        if attempted and nothing_active:
            return EnforcementMode.DISABLED
        if self.degraded_reasons:
            return EnforcementMode.DEGRADED
        return EnforcementMode.ENFORCED

    @property
    def degraded(self) -> bool:
        return self.mode is not EnforcementMode.ENFORCED

    def to_event(self) -> dict:
        """Serialise for a JSONL session event. Paths here are CLADA's own
        guardrail directories (not user secrets) so they are logged verbatim;
        any user-supplied path must be passed through :func:`redact_path`
        before reaching a log."""
        return {
            "mode": self.mode.value,
            "degraded": self.degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "hardened_paths": list(self.hardened_paths),
            "chmod_failures": list(self.chmod_failures),
            "restore_failures": list(self.restore_failures),
            "fswatch_available": self.fswatch_available,
            "fswatch_active": self.fswatch_active,
        }


def merge_statuses(*statuses: EnforcementStatus) -> EnforcementStatus:
    """Combine several partial statuses (e.g. harden + fswatch) into one."""
    merged = EnforcementStatus()
    for s in statuses:
        merged.chmod_attempted = merged.chmod_attempted or s.chmod_attempted
        merged.fswatch_attempted = merged.fswatch_attempted or s.fswatch_attempted
        merged.fswatch_available = merged.fswatch_available or s.fswatch_available
        merged.fswatch_active = merged.fswatch_active or s.fswatch_active
        merged.hardened_paths.extend(s.hardened_paths)
        merged.chmod_failures.extend(s.chmod_failures)
        merged.restore_failures.extend(s.restore_failures)
        for r in s.degraded_reasons:
            merged.add_reason(r)
    return merged


# ─────────────────────────────────────────────
# Fail-closed / owner-approval decision (POL-04)
# ─────────────────────────────────────────────
class EnforcementAction(str, Enum):
    PROCEED = "proceed"                       # fully enforced, go ahead
    PROCEED_WITH_APPROVAL = "proceed_with_approval"  # degraded but owner OK'd
    BLOCK = "block"                           # degraded, fail closed


@dataclass
class EnforcementDecision:
    action: EnforcementAction
    reason: str
    requires_owner_approval: bool = False

    @property
    def allowed(self) -> bool:
        return self.action is not EnforcementAction.BLOCK


def evaluate_enforcement(
    status: EnforcementStatus,
    *,
    owner_approved: bool = False,
    fail_closed: bool = True,
) -> EnforcementDecision:
    """Decide whether execution may proceed given an enforcement status.

    Contract (POL-04): when enforcement is degraded we either **fail closed**
    or require **explicit owner approval** — we never silently proceed as if
    fully hardened.

    * Not degraded            → PROCEED.
    * Degraded + owner_approved → PROCEED_WITH_APPROVAL.
    * Degraded + fail_closed   → BLOCK (and flag that approval would unblock).
    * Degraded + not fail_closed → PROCEED_WITH_APPROVAL, but still flagged so
      the caller logs it; "fail open" is opt-in, never the default.
    """
    if not status.degraded:
        return EnforcementDecision(EnforcementAction.PROCEED, "enforcement intact")

    reason = "degraded enforcement: " + "; ".join(status.degraded_reasons or ["unknown"])
    if owner_approved:
        return EnforcementDecision(
            EnforcementAction.PROCEED_WITH_APPROVAL,
            reason + " (owner approved)",
            requires_owner_approval=True,
        )
    if fail_closed:
        return EnforcementDecision(
            EnforcementAction.BLOCK,
            reason + " (failing closed; owner approval required to proceed)",
            requires_owner_approval=True,
        )
    return EnforcementDecision(
        EnforcementAction.PROCEED_WITH_APPROVAL,
        reason + " (fail-open configured)",
        requires_owner_approval=True,
    )


# ─────────────────────────────────────────────
# Redaction helpers (POL-05, LOG-03)
# ─────────────────────────────────────────────
REDACTED = "[REDACTED]"

# Secret-like value patterns. Order matters: more specific first. Each is
# replaced wholesale by REDACTED — we omit the value rather than masking part
# of it, because a partial mask can still leak entropy.
_SECRET_VALUE_PATTERNS: List[re.Pattern] = [
    # PEM private key blocks (multi-line).
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.DOTALL),
    # JWTs: three base64url segments.
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
    # Common provider token prefixes.
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"),       # OpenAI/Stripe-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),            # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),          # Slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),                # Google API key
]

# key=value / key: value assignments whose *key* names a secret. The value is
# omitted regardless of its shape.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|"
    r"PRIVATE[_-]?KEY|CREDENTIAL|AUTH)[A-Z0-9_]*)\s*([:=])\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def redact_text(text: Optional[str]) -> str:
    """Redact secret-like substrings from free text, preferring omission.

    Handles PEM key blocks, JWTs, known token prefixes and ``KEY=value``
    assignments where the key names a credential. Non-secret text is returned
    unchanged. ``None`` becomes ``""``.
    """
    if not text:
        return "" if text is None else text
    out = text
    for pat in _SECRET_VALUE_PATTERNS:
        out = pat.sub(REDACTED, out)
    out = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)} {REDACTED}", out)
    return out


def looks_like_secret(value: Optional[str]) -> bool:
    """Heuristic: does this standalone value look like a credential?"""
    if not value:
        return False
    return any(pat.search(value) for pat in _SECRET_VALUE_PATTERNS)


def redact_path(path, policy: Optional[PathPolicy] = None) -> str:
    """Return a log-safe representation of a path.

    If the path points at a protected-read (secret-bearing) file, omit the
    name entirely and emit only a generic marker — the *existence* of an env
    or key file is far less sensitive than its name, and omission beats
    leaking. Non-sensitive paths are returned as a clean POSIX string.
    """
    policy = policy or PathPolicy.default()
    if path is None:
        return ""
    if policy.is_protected_read(path):
        return "[redacted-sensitive-path]"
    return str(path).replace("\\", "/")


def redact_paths(paths: Iterable, policy: Optional[PathPolicy] = None) -> List[str]:
    """Redact a collection of paths (convenience for event payloads)."""
    policy = policy or PathPolicy.default()
    return [redact_path(p, policy) for p in paths]


# Type alias for the event sink callable used by FileAccessProxy.
EventSink = Callable[[str, dict], None]
