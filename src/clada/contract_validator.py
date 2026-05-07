#!/usr/bin/env python3
"""
CLADA Contract Validator
Validates contract.json against Meta-Schema and DR-xxx.md front-matter.
Used during Bootstrap (dual-lock) and PROPOSING (pre-execution check).
"""

import json, re, sys
from pathlib import Path
from datetime import datetime
from typing import Any

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    console = Console()
    HAS_RICH = True
except ImportError:
    console = None
    HAS_RICH = False


# ─────────────────────────────────────────────
# Meta-Schema: defines what a valid contract MUST contain
# Hard Fields = must be identical in dual-lock comparison
# Soft Fields = owner-choice on mismatch
# ─────────────────────────────────────────────
META_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CLADA Contract Meta-Schema",
    "type": "object",
    "required": [
        "contract_id", "version", "scope",
        "constraints", "hard_assertions"
    ],
    "properties": {
        "contract_id": {
            "type": "string",
            "pattern": "^CNT-\\d{3,}$",
            "description": "Unique contract ID, format: CNT-xxx"
        },
        "version": {
            "type": "string",
            "pattern": "^\\d+\\.\\d+\\.\\d+$",
            "description": "Semantic version: MAJOR.MINOR.PATCH"
        },
        "scope": {
            "type": "object",
            "required": ["modules", "interfaces"],
            "properties": {
                "modules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Module names covered by this contract"
                },
                "interfaces": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "API endpoints or function signatures"
                }
            }
        },
        "constraints": {
            "type": "object",
            "required": ["strict_types", "allowed_dependencies"],
            "properties": {
                "strict_types": {"type": "boolean"},
                "max_latency_ms": {"type": "number", "minimum": 0},
                "allowed_dependencies": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            }
        },
        "hard_assertions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description", "check_script"],
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": "^ASSERT-\\d+$"
                    },
                    "description": {"type": "string", "minLength": 1},
                    "check_script": {"type": "string", "minLength": 1}
                }
            }
        },
        "soft_recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description"],
                "properties": {
                    "id": {"type": "string", "pattern": "^REC-\\d+$"},
                    "description": {"type": "string"}
                }
            }
        },
        "superseded_by": {
            "anyOf": [
                {"type": "string", "pattern": "^CNT-\\d+$"},
                {"type": "null"}
            ]
        },
        "bootstrap_warning": {"type": "string"}
    },
    "additionalProperties": True  # allow extension fields
}

# Fields that MUST be identical in dual-lock comparison
HARD_FIELDS = [
    "contract_id",
    "version",
    "scope.modules",
    "scope.interfaces",
    "constraints.strict_types",
    "constraints.max_latency_ms",
    "constraints.allowed_dependencies",
    # hard_assertions ids and check_scripts
]

# Fields where Owner chooses on mismatch
SOFT_FIELDS = [
    "soft_recommendations",
    "bootstrap_warning",
    "scope.description",
]


# ─────────────────────────────────────────────
# Validation Result
# ─────────────────────────────────────────────
class ValidationResult:
    def __init__(self):
        self.valid = True
        self.errors: list[dict] = []    # hard failures
        self.warnings: list[dict] = []  # soft issues

    def add_error(self, field: str, message: str, value: Any = None):
        self.valid = False
        self.errors.append({"field": field, "message": message, "value": str(value)[:100]})

    def add_warning(self, field: str, message: str):
        self.warnings.append({"field": field, "message": message})

    def summary(self) -> str:
        if self.valid:
            return f"✅ VALID ({len(self.warnings)} warnings)"
        return f"❌ INVALID ({len(self.errors)} errors, {len(self.warnings)} warnings)"


# ─────────────────────────────────────────────
# Contract Validator
# ─────────────────────────────────────────────
class ContractValidator:

    def validate(self, contract: dict) -> ValidationResult:
        result = ValidationResult()

        # 1. JSON Schema validation
        if HAS_JSONSCHEMA:
            try:
                jsonschema.validate(instance=contract, schema=META_SCHEMA)
            except jsonschema.ValidationError as e:
                result.add_error(
                    e.json_path or str(e.path),
                    e.message,
                    e.instance
                )
            except jsonschema.SchemaError as e:
                result.add_error("meta-schema", f"Schema itself is invalid: {e.message}")
        else:
            # Fallback: manual checks
            self._manual_checks(contract, result)

        # 2. Semantic checks
        self._semantic_checks(contract, result)

        return result

    def _manual_checks(self, contract: dict, result: ValidationResult):
        """Fallback validation when jsonschema not available."""
        required = ["contract_id", "version", "scope", "constraints", "hard_assertions"]
        for field in required:
            if field not in contract:
                result.add_error(field, f"Required field '{field}' is missing")

        if "contract_id" in contract:
            if not re.match(r"^CNT-\d{3,}$", str(contract["contract_id"])):
                result.add_error("contract_id", "Must match CNT-xxx format", contract["contract_id"])

        if "version" in contract:
            if not re.match(r"^\d+\.\d+\.\d+$", str(contract["version"])):
                result.add_error("version", "Must be semantic version x.y.z", contract["version"])

        if "scope" in contract:
            scope = contract["scope"]
            if not isinstance(scope.get("modules"), list) or len(scope.get("modules", [])) == 0:
                result.add_error("scope.modules", "Must be non-empty array")
            if not isinstance(scope.get("interfaces"), list) or len(scope.get("interfaces", [])) == 0:
                result.add_error("scope.interfaces", "Must be non-empty array")

        if "hard_assertions" in contract:
            for i, assertion in enumerate(contract["hard_assertions"]):
                if not isinstance(assertion, dict):
                    result.add_error(f"hard_assertions[{i}]", "Must be object")
                    continue
                for key in ["id", "description", "check_script"]:
                    if key not in assertion:
                        result.add_error(f"hard_assertions[{i}].{key}", f"Required field missing")
                if "id" in assertion and not re.match(r"^ASSERT-\d+$", assertion["id"]):
                    result.add_error(f"hard_assertions[{i}].id", "Must match ASSERT-n format")

    def _semantic_checks(self, contract: dict, result: ValidationResult):
        """Business logic checks beyond schema."""
        # Check assertion IDs are unique
        if "hard_assertions" in contract:
            ids = [a.get("id") for a in contract["hard_assertions"] if isinstance(a, dict)]
            if len(ids) != len(set(ids)):
                result.add_error("hard_assertions", "Assertion IDs must be unique")

        # Warn if no performance constraint
        constraints = contract.get("constraints", {})
        if "max_latency_ms" not in constraints:
            result.add_warning(
                "constraints.max_latency_ms",
                "No latency constraint defined — Verifier cannot run perf assertions"
            )

        # Warn if no dependencies listed
        if not constraints.get("allowed_dependencies"):
            result.add_warning(
                "constraints.allowed_dependencies",
                "Empty dependency whitelist — Executor may introduce unauthorized packages"
            )

        # Bootstrap warning detection
        if "bootstrap_warning" in contract:
            result.add_warning(
                "bootstrap_warning",
                "This contract was created during Bootstrap and lacks historical ADR validation"
            )

        # Superseded check
        if contract.get("superseded_by"):
            result.add_warning(
                "superseded_by",
                f"This contract is superseded by {contract['superseded_by']} — should not be used"
            )


# ─────────────────────────────────────────────
# Dual-Lock Comparator
# ─────────────────────────────────────────────
class DualLockComparator:
    """
    Compares two contracts (from different models) at field level.
    Hard fields: must match exactly.
    Soft fields: owner chooses on mismatch.
    """

    def _get_nested(self, obj: dict, dotpath: str) -> Any:
        """Get nested value by dot-notation path."""
        parts = dotpath.split(".")
        cur = obj
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        return cur

    def compare(self, contract_a: dict, contract_b: dict) -> dict:
        """
        Returns:
        {
          "hard_conflicts": [{field, val_a, val_b}],  # must resolve
          "soft_conflicts": [{field, val_a, val_b}],  # owner's choice
          "matches": [field],
          "all_clear": bool
        }
        """
        hard_conflicts = []
        soft_conflicts = []
        matches = []

        # Check hard assertion check_scripts separately (ordered comparison)
        assertions_a = {a.get("id"): a.get("check_script")
                        for a in contract_a.get("hard_assertions", [])
                        if isinstance(a, dict)}
        assertions_b = {a.get("id"): a.get("check_script")
                        for a in contract_b.get("hard_assertions", [])
                        if isinstance(a, dict)}

        all_assertion_ids = set(assertions_a) | set(assertions_b)
        for aid in sorted(all_assertion_ids):
            va, vb = assertions_a.get(aid), assertions_b.get(aid)
            if va != vb:
                hard_conflicts.append({
                    "field": f"hard_assertions.{aid}.check_script",
                    "val_a": va, "val_b": vb
                })
            else:
                matches.append(f"hard_assertions.{aid}")

        # Check hard fields by dot-path
        for field in HARD_FIELDS:
            if "hard_assertions" in field:
                continue  # handled above
            va = self._get_nested(contract_a, field)
            vb = self._get_nested(contract_b, field)
            # Normalize lists for comparison
            if isinstance(va, list):
                va = sorted(va)
            if isinstance(vb, list):
                vb = sorted(vb)
            if va != vb:
                hard_conflicts.append({
                    "field": field,
                    "val_a": va,
                    "val_b": vb
                })
            else:
                matches.append(field)

        # Check soft fields
        for field in SOFT_FIELDS:
            va = self._get_nested(contract_a, field)
            vb = self._get_nested(contract_b, field)
            if va != vb:
                soft_conflicts.append({"field": field, "val_a": va, "val_b": vb})

        return {
            "hard_conflicts": hard_conflicts,
            "soft_conflicts": soft_conflicts,
            "matches": matches,
            "all_clear": len(hard_conflicts) == 0,
        }

    def interactive_resolve(self, comparison: dict) -> dict:
        """
        Walk the Owner through resolving hard conflicts.
        Returns merged contract fields.
        """
        resolutions = {}

        if comparison["all_clear"]:
            print("✅ All Hard Fields match — no owner intervention required.")
            return resolutions

        print(f"\n{'─'*60}")
        print(f"⚠️  {len(comparison['hard_conflicts'])} Hard Field conflict(s) require Owner decision:")
        print(f"{'─'*60}\n")

        for conflict in comparison["hard_conflicts"]:
            field = conflict["field"]
            print(f"Field: {field}")
            print(f"  Model A: {conflict['val_a']}")
            print(f"  Model B: {conflict['val_b']}")
            while True:
                choice = input("  Choose [A/B]: ").strip().upper()
                if choice == "A":
                    resolutions[field] = conflict["val_a"]
                    break
                elif choice == "B":
                    resolutions[field] = conflict["val_b"]
                    break
                else:
                    print("  Please enter A or B.")
            print()

        if comparison["soft_conflicts"]:
            print(f"\n{'─'*60}")
            print(f"ℹ️  {len(comparison['soft_conflicts'])} Soft Field difference(s) (optional):")
            for conflict in comparison["soft_conflicts"]:
                field = conflict["field"]
                print(f"\nField: {field}")
                print(f"  Model A: {conflict['val_a']}")
                print(f"  Model B: {conflict['val_b']}")
                choice = input("  Choose [A/B/skip]: ").strip().upper()
                if choice == "A":
                    resolutions[field] = conflict["val_a"]
                elif choice == "B":
                    resolutions[field] = conflict["val_b"]

        return resolutions


# ─────────────────────────────────────────────
# DR Validator
# ─────────────────────────────────────────────

DR_REQUIRED_FRONTMATTER = {"id", "title", "status", "date"}
DR_VALID_STATUSES = {"proposed", "accepted", "superseded"}
DR_REQUIRED_SECTIONS = ["## Context", "## Decision", "## Trade-offs"]

class DRValidator:

    def validate_file(self, path: Path) -> ValidationResult:
        result = ValidationResult()
        if not path.exists():
            result.add_error("file", f"DR file not found: {path}")
            return result

        content = path.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(content)

        # Required fields
        for field in DR_REQUIRED_FRONTMATTER:
            if field not in frontmatter:
                result.add_error(f"frontmatter.{field}", f"Required field '{field}' missing")

        # ID format
        if "id" in frontmatter:
            if not re.match(r"^DR-\d{3,}$", frontmatter["id"]):
                result.add_error("frontmatter.id", "Must match DR-xxx format", frontmatter["id"])
            # Check filename matches
            expected_stem = frontmatter["id"]
            if path.stem != expected_stem:
                result.add_warning("filename", f"Filename {path.stem} doesn't match ID {expected_stem}")

        # Status
        if "status" in frontmatter:
            if frontmatter["status"] not in DR_VALID_STATUSES:
                result.add_error(
                    "frontmatter.status",
                    f"Invalid status. Must be one of: {DR_VALID_STATUSES}",
                    frontmatter["status"]
                )
            # If superseded, must have superseded_by
            if frontmatter["status"] == "superseded" and not frontmatter.get("superseded_by"):
                result.add_error(
                    "frontmatter.superseded_by",
                    "status=superseded requires superseded_by field"
                )

        # Date format
        if "date" in frontmatter:
            try:
                datetime.strptime(frontmatter["date"], "%Y-%m-%d")
            except ValueError:
                result.add_error("frontmatter.date", "Must be YYYY-MM-DD format", frontmatter["date"])

        # Required body sections
        for section in DR_REQUIRED_SECTIONS:
            if section not in body:
                result.add_warning("body", f"Missing section: {section}")

        # Trade-offs quality check
        if "## Trade-offs" in body:
            tradeoff_idx = body.index("## Trade-offs")
            tradeoff_content = body[tradeoff_idx:tradeoff_idx+300]
            if len(tradeoff_content.strip()) < 50:
                result.add_warning(
                    "body.trade-offs",
                    "Trade-offs section too short — anti-hallucination requires explicit trade-off documentation"
                )

        return result

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML-ish frontmatter between --- delimiters."""
        fm = {}
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                raw_fm = parts[1].strip()
                body = parts[2].strip()
                for line in raw_fm.splitlines():
                    if ":" in line:
                        key, _, val = line.partition(":")
                        key = key.strip()
                        val = val.strip()
                        # Handle null
                        if val.lower() == "null" or val == "":
                            fm[key] = None
                        # Handle lists [a, b, c]
                        elif val.startswith("[") and val.endswith("]"):
                            items = [v.strip().strip('"\'') for v in val[1:-1].split(",")]
                            fm[key] = [i for i in items if i]
                        else:
                            fm[key] = val.strip('"\'')
        return fm, body

    def validate_index(self, decisions_dir: Path) -> dict:
        """
        Validate all DR files in a directory.
        Returns summary including superseded chains.
        """
        results = {}
        dr_files = sorted(decisions_dir.glob("DR-*.md"))

        for dr_file in dr_files:
            r = self.validate_file(dr_file)
            results[dr_file.name] = {
                "valid": r.valid,
                "errors": r.errors,
                "warnings": r.warnings,
            }

        return {
            "total": len(dr_files),
            "valid_count": sum(1 for r in results.values() if r["valid"]),
            "results": results,
        }


# ─────────────────────────────────────────────
# L2 Index Builder
# ─────────────────────────────────────────────
class L2IndexBuilder:
    """
    Builds/updates the L2 index.json from all DR files.
    This index is the Gateway's RAG pre-filter — superseded records
    are excluded before being fed to any Agent.
    """

    def __init__(self, decisions_dir: Path):
        self.decisions_dir = decisions_dir
        self.index_path = decisions_dir / "index.json"
        self.dr_validator = DRValidator()

    def rebuild(self) -> dict:
        index = {"decisions": [], "contracts": [], "last_updated": datetime.now().isoformat()}
        dr_files = sorted(self.decisions_dir.glob("DR-*.md"))

        for dr_file in dr_files:
            fm, _ = self.dr_validator._parse_frontmatter(dr_file.read_text())
            if not fm.get("id"):
                continue
            index["decisions"].append({
                "id":            fm.get("id"),
                "title":         fm.get("title", ""),
                "status":        fm.get("status", "proposed"),
                "superseded_by": fm.get("superseded_by"),
                "date":          fm.get("date"),
                "tags":          fm.get("tags", []),
                "summary":       self._extract_summary(dr_file),
            })

        # Load contract info from spec/
        spec_dir = self.decisions_dir.parent / "spec"
        contract_file = spec_dir / "contract.json"
        if contract_file.exists():
            try:
                c = json.loads(contract_file.read_text())
                index["contracts"].append({
                    "id":      c.get("contract_id"),
                    "version": c.get("version"),
                    "status":  "superseded" if c.get("superseded_by") else "active",
                })
            except Exception:
                pass

        index["total"] = len(index["decisions"])
        self.index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False))
        return index

    def _extract_summary(self, dr_file: Path) -> str:
        """Extract first sentence of ## Decision section as summary."""
        _, body = self.dr_validator._parse_frontmatter(dr_file.read_text())
        if "## Decision" in body:
            idx = body.index("## Decision")
            section = body[idx+len("## Decision"):].strip()
            # Get first non-empty line
            for line in section.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:150]
        return ""

    def get_active_decisions(self) -> list[dict]:
        """Return only non-superseded decisions."""
        if not self.index_path.exists():
            return []
        try:
            index = json.loads(self.index_path.read_text())
            return [d for d in index.get("decisions", [])
                    if d.get("status") != "superseded"]
        except Exception:
            return []


# ─────────────────────────────────────────────
# Report Printer
# ─────────────────────────────────────────────
def print_validation_report(result: ValidationResult, title: str = "Validation Report"):
    if HAS_RICH and console:
        color = "green" if result.valid else "red"
        console.print(Panel(
            f"[bold]{result.summary()}[/bold]",
            title=f"[bold]{title}[/bold]",
            border_style=color
        ))
        if result.errors:
            t = Table("Field", "Error", "Value", box=box.SIMPLE, show_header=True)
            for e in result.errors:
                t.add_row(f"[red]{e['field']}[/red]", e["message"], f"[dim]{e['value']}[/dim]")
            console.print(t)
        if result.warnings:
            t = Table("Field", "Warning", box=box.SIMPLE, show_header=True)
            for w in result.warnings:
                t.add_row(f"[yellow]{w['field']}[/yellow]", w["message"])
            console.print(t)
    else:
        print(f"\n{title}: {result.summary()}")
        for e in result.errors:
            print(f"  ERROR [{e['field']}]: {e['message']}")
        for w in result.warnings:
            print(f"  WARN  [{w['field']}]: {w['message']}")


# ─────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="CLADA Contract & DR Validator")
    parser.add_argument("command", choices=["contract", "dr", "index", "dual-lock"])
    parser.add_argument("path", nargs="?", help="File or directory path")
    parser.add_argument("--contract-b", help="Second contract for dual-lock comparison")
    args = parser.parse_args()

    validator = ContractValidator()
    dr_validator = DRValidator()

    if args.command == "contract":
        if not args.path:
            print("Usage: contract_validator.py contract <path/to/contract.json>")
            sys.exit(1)
        p = Path(args.path)
        if not p.exists():
            print(f"File not found: {p}")
            sys.exit(1)
        try:
            contract = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
            sys.exit(1)
        result = validator.validate(contract)
        print_validation_report(result, f"Contract: {p.name}")
        sys.exit(0 if result.valid else 1)

    elif args.command == "dr":
        if not args.path:
            print("Usage: contract_validator.py dr <path/to/DR-xxx.md>")
            sys.exit(1)
        result = dr_validator.validate_file(Path(args.path))
        print_validation_report(result, f"DR: {Path(args.path).name}")
        sys.exit(0 if result.valid else 1)

    elif args.command == "index":
        d = Path(args.path) if args.path else Path("docs/decisions")
        builder = L2IndexBuilder(d)
        index = builder.rebuild()
        print(f"✅ L2 index rebuilt: {len(index['decisions'])} decisions")
        active = builder.get_active_decisions()
        print(f"   Active (non-superseded): {len(active)}")
        sys.exit(0)

    elif args.command == "dual-lock":
        if not args.path or not args.contract_b:
            print("Usage: contract_validator.py dual-lock <contract_a.json> --contract-b <contract_b.json>")
            sys.exit(1)
        try:
            ca = json.loads(Path(args.path).read_text())
            cb = json.loads(Path(args.contract_b).read_text())
        except Exception as e:
            print(f"Error reading contracts: {e}")
            sys.exit(1)
        comparator = DualLockComparator()
        comparison = comparator.compare(ca, cb)
        print(f"\nDual-Lock Comparison:")
        print(f"  Hard conflicts: {len(comparison['hard_conflicts'])}")
        print(f"  Soft conflicts: {len(comparison['soft_conflicts'])}")
        print(f"  Matches:        {len(comparison['matches'])}")
        if not comparison["all_clear"]:
            resolutions = comparator.interactive_resolve(comparison)
            print(f"\nResolutions recorded: {len(resolutions)}")
        sys.exit(0 if comparison["all_clear"] else 1)


if __name__ == "__main__":
    main()
