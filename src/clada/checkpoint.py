#!/usr/bin/env python3
"""Session-scoped git checkpoint helpers.

CLADA must never treat the whole working tree as session-owned. These helpers
record the git status before a session and compare it with the status after the
session so audit and rollback guidance can separate pre-existing owner work
from files that appeared or changed during the session.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Set


@dataclass(frozen=True)
class GitStatusEntry:
    path: str
    index: str
    worktree: str


@dataclass
class SessionCheckpoint:
    root: Path
    git_available: bool
    before: Dict[str, GitStatusEntry] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def capture(cls, root: Path) -> "SessionCheckpoint":
        root = Path(root)
        status, error = git_status(root)
        return cls(
            root=root,
            git_available=error is None,
            before=status,
            error=error,
        )

    def diff(self) -> "CheckpointDiff":
        after, error = git_status(self.root)
        git_available = self.git_available and error is None
        before_paths = set(self.before)
        after_paths = set(after)

        session_owned = sorted(
            path for path in after_paths
            if path not in before_paths or after[path] != self.before[path]
        )
        pre_existing = sorted(path for path in before_paths if path in after_paths)
        resolved_pre_existing = sorted(path for path in before_paths - after_paths)

        return CheckpointDiff(
            root=self.root,
            git_available=git_available,
            session_owned_paths=session_owned,
            pre_existing_dirty_paths=pre_existing,
            resolved_pre_existing_paths=resolved_pre_existing,
            after=after,
            error=error or self.error,
        )


@dataclass
class CheckpointDiff:
    root: Path
    git_available: bool
    session_owned_paths: List[str]
    pre_existing_dirty_paths: List[str]
    resolved_pre_existing_paths: List[str]
    after: Dict[str, GitStatusEntry] = field(default_factory=dict)
    error: str | None = None

    def safe_stage_args(self) -> List[str]:
        """Return explicit pathspec args for staging session-owned paths only."""
        return list(self.session_owned_paths)

    def rollback_commands(self) -> List[str]:
        if not self.git_available:
            return ["Git status unavailable; inspect the working tree manually."]
        if not self.session_owned_paths:
            return ["No session-owned file changes were detected."]
        untracked = [
            path for path in self.session_owned_paths
            if self.after.get(path)
            and self.after[path].index == "?"
            and self.after[path].worktree == "?"
        ]
        tracked = [path for path in self.session_owned_paths if path not in untracked]
        commands: List[str] = []
        if tracked:
            quoted = " ".join(_shell_quote(path) for path in tracked)
            commands.extend([
                f"git restore --staged -- {quoted}",
                f"git restore -- {quoted}",
            ])
        if untracked:
            quoted = " ".join(_shell_quote(path) for path in untracked)
            commands.append(f"git clean -f -- {quoted}")
        return commands

    def to_event(self) -> dict:
        return {
            "git_available": self.git_available,
            "session_owned_paths": list(self.session_owned_paths),
            "pre_existing_dirty_paths": list(self.pre_existing_dirty_paths),
            "resolved_pre_existing_paths": list(self.resolved_pre_existing_paths),
            "error": self.error,
        }


def git_status(root: Path) -> tuple[Dict[str, GitStatusEntry], str | None]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {}, str(exc)

    if result.returncode != 0:
        return {}, (result.stderr or result.stdout or "git status failed").strip()
    return parse_porcelain(result.stdout.splitlines()), None


def parse_porcelain(lines: Iterable[str]) -> Dict[str, GitStatusEntry]:
    entries: Dict[str, GitStatusEntry] = {}
    for line in lines:
        if not line:
            continue
        index = line[0]
        worktree = line[1] if len(line) > 1 else " "
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip()
        if not path:
            continue
        entries[path] = GitStatusEntry(path=path, index=index, worktree=worktree)
    return entries


def stage_session_paths(root: Path, paths: Iterable[str]) -> subprocess.CompletedProcess:
    explicit = [path for path in paths if path]
    if not explicit:
        return subprocess.CompletedProcess(["git", "add", "--"], 0, "", "")
    return subprocess.run(
        ["git", "add", "--"] + explicit,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
