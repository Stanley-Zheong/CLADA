#!/usr/bin/env python3
"""
CLADA Bootstrap + Memory Manager
- bootstrap.py: clada init flow (dual-lock contract generation)
- memory_manager.py: L2 index maintenance + iteration archival
"""

import json, subprocess, time
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    console = Console()
    HAS_RICH = True
except ImportError:
    console = None
    HAS_RICH = False

from clada.contract_validator import (
    ContractValidator, DualLockComparator,
    DRValidator, L2IndexBuilder,
    print_validation_report
)

CLADA_ROOT = Path(__file__).parent.parent.parent
DECISIONS_DIR = CLADA_ROOT / "docs" / "decisions"
SPEC_DIR = CLADA_ROOT / "docs" / "spec"


# ─────────────────────────────────────────────
# Bootstrap: clada init
# ─────────────────────────────────────────────
def run_bootstrap(runtime, proxy):
    """
    Full Bootstrap sequence:
    1. Collect Goal from Owner
    2. Generate contract via two model calls (dual-lock)
    3. Owner resolves conflicts
    4. Write DR-001.md + contract.json
    5. Build initial L2 index
    """
    _section("CLADA Bootstrap — Creating Project Constitution")

    # ── Step 1: Goal input ──────────────────────────────
    print("\n[1/6] Define your project Goal.")
    print("      Be specific: what will this system DO and what will it NEVER DO?\n")
    goal = _multiline_input("Goal (end with empty line): ")

    if not goal.strip():
        _log("Bootstrap cancelled — no goal provided.", "red")
        return

    # ── Step 2: Tech stack ──────────────────────────────
    print("\n[2/6] Technical context (optional — press Enter to skip).")
    stack = input("Language/Framework (e.g. TypeScript + Node.js): ").strip()
    modules_raw = input("Initial modules (comma-separated, e.g. auth,db,api): ").strip()
    modules = [m.strip() for m in modules_raw.split(",") if m.strip()] or ["core"]

    # ── Step 3: Generate contracts (dual-lock) ──────────
    _section("[3/6] Dual-Lock Contract Generation")

    # Show configured models from .clada/config.yml
    try:
        from clada.config import CLADAConfig
        cfg = CLADAConfig.load()
        model_a, model_b = cfg.get_bootstrap_pair()
        model_a_label = f"{model_a.provider}/{model_a.model}"
        model_b_label = f"{model_b.provider}/{model_b.model}"
    except Exception:
        model_a_label = "Model A"
        model_b_label = "Model B"

    print(f"Configured: {model_a_label} vs {model_b_label}")
    print("(In production: calls two different LLM APIs. In Bootstrap mode: guided manual entry.)\n")

    print(f"─── {model_a_label} (Primary) ───")
    contract_a = _guided_contract_entry("A", goal, stack, modules)

    print(f"\n─── {model_b_label} (Cross-check) ───")
    print("Enter the same contract from a different perspective (or copy A to fast-track).")
    use_same = input("Use identical contract for Model B? [y/N]: ").strip().lower()
    if use_same == "y":
        contract_b = dict(contract_a)
        # Simulate slight difference for demo
    else:
        contract_b = _guided_contract_entry("B", goal, stack, modules)

    # ── Step 4: Validate each contract ──────────────────
    _section("[4/6] Validating Contracts")
    validator = ContractValidator()

    result_a = validator.validate(contract_a)
    print_validation_report(result_a, "Model A Contract")
    if not result_a.valid:
        _log("Model A contract is invalid. Please fix errors and retry.", "red")
        return

    result_b = validator.validate(contract_b)
    print_validation_report(result_b, "Model B Contract")
    if not result_b.valid:
        _log("Model B contract is invalid. Please fix errors and retry.", "red")
        return

    # ── Step 5: Dual-lock comparison ────────────────────
    _section("[5/6] Dual-Lock Comparison")
    comparator = DualLockComparator()
    comparison = comparator.compare(contract_a, contract_b)

    print(f"Hard conflicts: {len(comparison['hard_conflicts'])}")
    print(f"Soft conflicts: {len(comparison['soft_conflicts'])}")
    print(f"Matches:        {len(comparison['matches'])}")

    resolutions = {}
    if not comparison["all_clear"]:
        resolutions = comparator.interactive_resolve(comparison)

    # Build final contract from Model A + resolutions
    final_contract = dict(contract_a)
    _apply_resolutions(final_contract, resolutions)
    final_contract["bootstrap_warning"] = (
        "Created during Bootstrap — lacks historical ADR validation. "
        "Owner manually confirmed on " + datetime.now().strftime("%Y-%m-%d")
    )

    # ── Step 6: Owner confirmation ───────────────────────
    _section("[6/6] Final Owner Confirmation")
    print("\nFinal contract to be committed:")
    print(json.dumps(final_contract, indent=2, ensure_ascii=False)[:2000])
    print("\n⚠️  Review this contract carefully — it is your project constitution.")
    confirm = input("\nConfirm and commit? [yes/NO]: ").strip().lower()

    if confirm != "yes":
        _log("Bootstrap cancelled by Owner.", "yellow")
        return

    # ── Write files ──────────────────────────────────────
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)

    contract_path = SPEC_DIR / "contract.json"
    contract_path.write_text(json.dumps(final_contract, indent=2, ensure_ascii=False))
    _log(f"✅ contract.json written → {contract_path}", "green")

    # Write .goal.md
    goal_path = DECISIONS_DIR / ".goal.md"
    goal_path.write_text(f"# Project Goal\n\n{goal}\n\n## Tech Stack\n{stack or 'Not specified'}\n")

    # Write DR-001
    _write_dr001(goal, stack, modules, final_contract)

    # Build L2 index
    builder = L2IndexBuilder(DECISIONS_DIR)
    index = builder.rebuild()
    _log(f"✅ L2 index built: {index['total']} decisions", "green")

    # Update runtime state
    if runtime:
        runtime.transition_to_idle = True
        runtime.save()

    _section("Bootstrap Complete")
    print("Project constitution is in place.")
    print("Next step: run /propose to start your first iteration.\n")


def _guided_contract_entry(model_label: str, goal: str, stack: str, modules: list) -> dict:
    """Interactive guided contract entry for bootstrap."""
    print(f"\nBuilding contract for Model {model_label}...")
    print(f"(Auto-filling from Goal: '{goal[:60]}...')\n")

    # Auto-generate contract ID
    existing = list((SPEC_DIR).glob("contract*.json")) if SPEC_DIR.exists() else []
    contract_num = len(existing) + 1
    contract_id = f"CNT-{contract_num:03d}"

    # Generate initial interfaces from Goal
    interfaces = []
    iface_raw = input(f"  API interfaces (comma-sep, e.g. 'POST /login, GET /users'): ").strip()
    if iface_raw:
        interfaces = [i.strip() for i in iface_raw.split(",") if i.strip()]
    if not interfaces:
        interfaces = ["POST /api/v1/main"]

    # Dependencies
    deps_raw = input(f"  Allowed dependencies (comma-sep): ").strip()
    deps = [d.strip() for d in deps_raw.split(",") if d.strip()]

    # Assertions
    assertions = []
    print(f"  Add hard assertions (format: 'description | check_script')")
    print(f"  Example: 'No plaintext passwords | npm run test:security'")
    print(f"  (Empty line to finish)")
    i = 1
    while True:
        line = input(f"  ASSERT-{i:02d}: ").strip()
        if not line:
            break
        if "|" in line:
            desc, _, script = line.partition("|")
            assertions.append({
                "id": f"ASSERT-{i:02d}",
                "description": desc.strip(),
                "check_script": script.strip()
            })
        else:
            assertions.append({
                "id": f"ASSERT-{i:02d}",
                "description": line,
                "check_script": "npm test"
            })
        i += 1

    if not assertions:
        assertions = [{
            "id": "ASSERT-01",
            "description": "All tests must pass",
            "check_script": "npm test"
        }]

    latency_raw = input("  Max latency ms (Enter to skip): ").strip()
    constraints = {
        "strict_types": True,
        "allowed_dependencies": deps,
    }
    if latency_raw.isdigit():
        constraints["max_latency_ms"] = int(latency_raw)

    return {
        "contract_id": contract_id,
        "version": "1.0.0",
        "scope": {
            "modules": modules,
            "interfaces": interfaces,
        },
        "constraints": constraints,
        "hard_assertions": assertions,
        "soft_recommendations": [],
        "superseded_by": None,
    }


def _write_dr001(goal: str, stack: str, modules: list, contract: dict):
    """Write the initial DR-001 (tech stack & architecture decision)."""
    dr_path = DECISIONS_DIR / "DR-001.md"
    content = f"""---
id: DR-001
title: Initial Project Constitution — Tech Stack and Architecture
status: accepted
superseded_by: null
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [bootstrap, architecture, tech-stack]
related_contract: {contract.get("contract_id", "CNT-001")}
---

## Context
Project bootstrapped via CLADA /init. This is the foundational decision record
establishing the technical constraints for all subsequent iterations.

## Decision
- Language/Framework: {stack or "Not specified"}
- Initial modules: {", ".join(modules)}
- Contract: {contract.get("contract_id")} v{contract.get("version")}
- Hard assertions: {len(contract.get("hard_assertions", []))} defined

## Trade-offs
- Adopting CLADA's strict Contract-first approach limits developer freedom
  but prevents spec drift over long iteration cycles.
- Dual-lock bootstrap may slow initial setup but prevents single-model bias
  in the project constitution.

## Consequences
- ✅ All future iterations are bound by contract.json
- ✅ Verifier has a machine-readable audit standard
- ⚠️  This DR was created during Bootstrap — no historical ADR to reference

## Verification
- Run: python3 contract_validator.py contract docs/spec/contract.json
- All subsequent DRs must reference this foundational decision

---
*Project Goal:*
> {goal[:500]}
"""
    dr_path.write_text(content, encoding="utf-8")
    _log(f"✅ DR-001.md written → {dr_path}", "green")


def _apply_resolutions(contract: dict, resolutions: dict):
    """Apply dual-lock resolution choices to contract dict."""
    for dotpath, value in resolutions.items():
        parts = dotpath.split(".")
        target = contract
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value


# ─────────────────────────────────────────────
# Memory Manager: L2 + L3 maintenance
# ─────────────────────────────────────────────
class MemoryManager:
    """
    Manages the three-tier memory system:
    - L2: Structural (DR index + architecture.md)
    - L3: Historical (iteration archives → future vector DB)
    """

    def __init__(self, root: Path = CLADA_ROOT):
        self.root = root
        self.decisions_dir = root / "docs" / "decisions"
        self.iterations_dir = root / "docs" / "iterations"
        self.runtime_dir = root / "runtime"
        self.index_builder = L2IndexBuilder(self.decisions_dir)

    def rebuild_l2(self) -> dict:
        """Rebuild the L2 index from all DR files."""
        index = self.index_builder.rebuild()
        _log(f"[L2] Index rebuilt: {index['total']} decisions, "
             f"{sum(1 for d in index['decisions'] if d.get('status') != 'superseded')} active", "green")
        return index

    def get_context_for_agent(self, query: str = "", max_decisions: int = 5) -> str:
        """
        Build the L2 context string to inject into an Agent's system prompt.
        Filters out superseded decisions, limits to most relevant.
        """
        active = self.index_builder.get_active_decisions()
        if not active:
            return "No ADR history available."

        # Simple relevance: if query matches tags or title, boost
        if query:
            def relevance(d):
                score = 0
                q_lower = query.lower()
                if q_lower in d.get("title", "").lower():
                    score += 3
                for tag in d.get("tags", []):
                    if q_lower in tag.lower():
                        score += 2
                return score
            active = sorted(active, key=relevance, reverse=True)

        # Most recent first (by date), limited count
        active = sorted(active, key=lambda d: d.get("date", ""), reverse=True)[:max_decisions]

        lines = ["## Active Decision Records (L2 Context)\n"]
        for d in active:
            lines.append(f"**{d['id']}** ({d['date']}): {d['title']}")
            if d.get("summary"):
                lines.append(f"  → {d['summary']}")
            lines.append("")

        return "\n".join(lines)

    def archive_iteration(self, iteration_id: str, runtime_state: dict) -> Path:
        """
        Archive a completed iteration to docs/iterations/IT-xxx.md.
        This feeds L3 (historical vector DB in future phases).
        """
        self.iterations_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.iterations_dir / f"{iteration_id}.md"

        sections = [
            f"# {iteration_id} — Archived {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
            f"## Runtime State\n```json\n{json.dumps(runtime_state, indent=2)}\n```\n",
        ]

        # Append audit report if exists
        audit_file = self.root / ".comm" / "audit_report.json"
        if audit_file.exists():
            sections.append(
                f"## Audit Report\n```json\n{audit_file.read_text()}\n```\n"
            )

        # Append progress log if exists
        progress_file = self.runtime_dir / "current_progress.md"
        if progress_file.exists():
            sections.append(
                f"## Progress Log\n{progress_file.read_text()}\n"
            )

        archive_path.write_text("".join(sections), encoding="utf-8")
        _log(f"[L2] Iteration {iteration_id} archived → {archive_path}", "dim")
        return archive_path

    def compaction_prompt(self, iteration_id: str) -> str:
        """
        Returns the Verifier prompt for compressing an iteration into ADR summary.
        Use this with the Verifier LLM call to generate DR entries from verbose logs.
        """
        archive_path = self.iterations_dir / f"{iteration_id}.md"
        archive_content = ""
        if archive_path.exists():
            archive_content = archive_path.read_text()[:3000]

        return f"""You are the CLADA Chief Architect. Summarize the following iteration log
into exactly 3 core architectural decisions. For each decision, output a complete
DR-xxx.md front-matter block plus the 4 required sections (Context, Decision,
Trade-offs, Consequences). Be concise. Delete irrelevant implementation details.
Keep only decisions that affect future architecture.

ITERATION LOG:
{archive_content}

Output format: Three separate DR markdown files, separated by ---FILE_SEPARATOR---
"""

    def scan_repo_for_architecture(self) -> str:
        """
        Cold-start L3: scan existing codebase to generate initial architecture.md.
        Used when L3 is empty on a non-new project.
        """
        src = self.root / "src"
        if not src.exists():
            return "No src/ directory found."

        # Collect file tree (limited depth)
        tree_lines = ["## Repository Structure\n"]
        for p in sorted(src.rglob("*"))[:50]:
            if p.is_file():
                rel = p.relative_to(self.root)
                tree_lines.append(f"  {rel}")

        arch_path = self.root / "docs" / "architecture.md"
        arch_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"# Architecture Overview\n"
            f"*Auto-generated by CLADA cold-start scan — {datetime.now().strftime('%Y-%m-%d')}*\n\n"
            + "\n".join(tree_lines)
        )
        arch_path.write_text(content)
        _log(f"[L3] Cold-start architecture.md generated → {arch_path}", "green")
        return content

    def update_progress(self, trace_text: str):
        """Append a TRACE line to current_progress.md."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        progress_file = self.runtime_dir / "current_progress.md"
        ts = datetime.now().strftime("%H:%M:%S")
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(f"{ts} {trace_text}\n")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _log(msg: str, color: str = "white"):
    ts = datetime.now().strftime("%H:%M:%S")
    if HAS_RICH and console:
        console.print(f"[dim]{ts}[/dim] {msg}", style=color)
    else:
        print(f"{ts} {msg}")

def _section(title: str):
    if HAS_RICH and console:
        console.print(f"\n[bold cyan]{'─'*4} {title} {'─'*4}[/bold cyan]")
    else:
        print(f"\n{'─'*50}\n{title}\n{'─'*50}")

def _multiline_input(prompt: str) -> str:
    print(prompt, end="", flush=True)
    lines = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: bootstrap.py [init|rebuild-l2|archive <IT-xxx>|cold-start]")
        sys.exit(1)

    cmd = sys.argv[1]
    mm = MemoryManager()

    if cmd == "init":
        run_bootstrap(runtime=None, proxy=None)
    elif cmd == "rebuild-l2":
        mm.rebuild_l2()
    elif cmd == "archive" and len(sys.argv) > 2:
        mm.archive_iteration(sys.argv[2], {})
    elif cmd == "cold-start":
        mm.scan_repo_for_architecture()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
