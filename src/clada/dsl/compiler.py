"""
DSL Compiler — translates S-expression DSL into CLADA contract.json + spec.md.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from clada.dsl.parser import parse_string, SExpr
from clada.dsl.registry import DSLRegistry


@dataclass
class CompileResult:
    """Output of DSL compilation."""
    success: bool
    contract: dict = field(default_factory=dict)
    spec: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    domain: str = "general"

    def write_to(self, spec_dir: Path, decisions_dir: Path):
        """Write compiled contract.json and current_spec.md to disk."""
        spec_dir.mkdir(parents=True, exist_ok=True)
        decisions_dir.mkdir(parents=True, exist_ok=True)

        contract_path = spec_dir / "contract.json"
        contract_path.write_text(json.dumps(self.contract, indent=2, ensure_ascii=False))

        spec_path = spec_dir / "current_spec.md"
        spec_path.write_text(self.spec, encoding="utf-8")

        return contract_path, spec_path


class DSLCompiler:
    """
    Compiles CLADA DSL (S-expression) into contract.json and spec.md.

    DSL structure:
      (domain <name>)
      (module <name> <description> ...)
      (requirement <id> <title> <details>)
      (invariant <id> <description> <enforcement> ...)
      (test <id> <covers> ...)
      (contract <name> <version> ...)
      (state-machine <name> ...)
      (pipeline <name> ...)
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.root = project_root or Path(".")

    def compile(self, source: str, source_path: str = "<string>") -> CompileResult:
        """Compile a DSL source string into contract.json + spec.md."""
        result = CompileResult(success=False)

        try:
            exprs = parse_string(source)
        except Exception as e:
            result.errors.append(f"Parse error: {e}")
            return result

        if not exprs:
            result.errors.append("Empty or invalid DSL source")
            return result

        # Extract domain declaration
        domain_name = "general"
        modules = []
        requirements = []
        invariants = []
        tests = []
        contracts = []
        state_machines = []
        pipelines = []
        meta = {}

        for expr in exprs:
            if not isinstance(expr, list) or len(expr) < 1:
                continue
            tag = expr[0] if isinstance(expr[0], str) else ""

            if tag == "domain":
                domain_name = expr[1] if len(expr) > 1 else "general"
                result.domain = domain_name
            elif tag == "module":
                modules.append(self._parse_module(expr))
            elif tag == "requirement":
                requirements.append(self._parse_requirement(expr))
            elif tag == "invariant":
                invariants.append(self._parse_invariant(expr))
            elif tag == "test":
                tests.append(self._parse_test(expr))
            elif tag == "contract":
                contracts.append(self._parse_contract(expr))
            elif tag == "state-machine":
                state_machines.append(self._parse_state_machine(expr))
            elif tag == "pipeline":
                pipelines.append(self._parse_pipeline(expr))
            elif tag == "meta":
                meta = self._parse_dict_body(expr[1:]) if len(expr) > 1 else {}

        # Load domain schema
        domain_def = DSLRegistry.get(domain_name)
        if domain_def:
            result.warnings.append(f"Domain '{domain_name}': {domain_def.get('description', '')}")

        # Build contract.json
        result.contract = self._build_contract(
            domain_name, modules, requirements, invariants, contracts, meta
        )

        # Build spec.md
        result.spec = self._build_spec(
            domain_name, modules, requirements, invariants,
            tests, state_machines, pipelines, meta
        )

        result.success = len(result.errors) == 0
        return result

    def compile_file(self, path: Path) -> CompileResult:
        """Compile a .dsl file."""
        source = path.read_text(encoding="utf-8")
        return self.compile(source, str(path))

    # ── Parsers for each DSL form ──────────────────────────
    # Each form is [tag, *positionals, {kv_dict}] from the parser.

    def _get_body(self, expr: list, start: int = 1) -> dict:
        """Extract key-value dict from the expression, starting at index."""
        for item in expr[start:]:
            if isinstance(item, dict):
                return item
        return {}

    def _parse_module(self, expr: list) -> dict:
        name = str(expr[1]) if len(expr) > 1 and not isinstance(expr[1], dict) else "unnamed"
        body = self._get_body(expr, 2)
        # If expr[2] is a plain string (positional description), use it
        if isinstance(expr[2], str) and len(expr) > 2:
            body.setdefault("description", str(expr[2]))
        return {"name": name, **body}

    def _parse_requirement(self, expr: list) -> dict:
        req_id = str(expr[1]) if len(expr) > 1 and not isinstance(expr[1], dict) else ""
        body = self._get_body(expr, 2)
        # title from dict takes priority, fall back to expr[2] if it's a plain string
        title = body.pop("title", None)
        if title is None and len(expr) > 2 and isinstance(expr[2], str):
            title = str(expr[2])
        return {"id": req_id, "title": str(title) if title else "", **body}

    def _parse_invariant(self, expr: list) -> dict:
        inv_id = str(expr[1]) if len(expr) > 1 and not isinstance(expr[1], dict) else ""
        body = self._get_body(expr, 2)
        desc = body.pop("description", None)
        if desc is None and len(expr) > 2 and isinstance(expr[2], str):
            desc = str(expr[2])
        return {"id": inv_id, "description": str(desc) if desc else "", **body}

    def _parse_test(self, expr: list) -> dict:
        test = {"id": str(expr[1]) if len(expr) > 1 else ""}
        body = self._get_body(expr, 2)
        test.update(body)
        return test

    def _parse_contract(self, expr: list) -> dict:
        c = {"name": str(expr[1]) if len(expr) > 1 else "default"}
        body = self._get_body(expr, 2)
        c.update(body)
        return c

    def _parse_state_machine(self, expr: list) -> dict:
        sm = {"name": str(expr[1]) if len(expr) > 1 else "default"}
        body = self._get_body(expr, 2)
        sm.update(body)
        return sm

    def _parse_pipeline(self, expr: list) -> dict:
        p = {"name": str(expr[1]) if len(expr) > 1 else "default"}
        body = self._get_body(expr, 2)
        p.update(body)
        return p

    # ── Contract builder ───────────────────────────────────

    def _build_contract(self, domain: str, modules: list, requirements: list,
                        invariants: list, contracts: list, meta: dict) -> dict:
        """Build a contract.json dict from parsed DSL data."""

        module_names = [m["name"] for m in modules] or ["core"]
        interfaces = [r.get("id", "") for r in requirements] or ["POST /api/v1/main"]
        if not interfaces:
            interfaces = ["POST /api/v1/main"]

        hard_assertions = []
        soft_recommendations = []
        for i, inv in enumerate(invariants):
            desc = inv.get("description", "")
            if isinstance(desc, dict):
                desc = desc.get("description", str(desc)[:100])
            desc = str(desc)
            check = inv.get("check", inv.get("check_script", "npm test"))
            enforcement = inv.get("enforcement", "hard")
            if enforcement == "hard":
                hard_assertions.append({
                    "id": f"ASSERT-{i+1:02d}",
                    "description": desc,
                    "check_script": str(check),
                })
            else:
                soft_recommendations.append({
                    "id": f"REC-{i+1:02d}",
                    "description": desc,
                })

        if not hard_assertions:
            hard_assertions.append({
                "id": "ASSERT-01",
                "description": "All tests must pass",
                "check_script": "npm test",
            })

        deps = meta.get("dependencies", meta.get("allowed_dependencies", []))
        if isinstance(deps, str):
            deps = [d.strip() for d in deps.split(",") if d.strip()]

        contract_data = {
            "contract_id": f"CNT-001",
            "version": contracts[0].get("version", "1.0.0") if contracts else "1.0.0",
            "scope": {
                "modules": module_names,
                "interfaces": interfaces,
            },
            "constraints": {
                "strict_types": meta.get("strict_types", True),
                "allowed_dependencies": deps,
            },
            "hard_assertions": hard_assertions,
            "soft_recommendations": [],
            "superseded_by": None,
            "bootstrap_warning": (
                f"Generated from DSL ({domain}) on "
                f"{datetime.now().strftime('%Y-%m-%d')}. "
                "Review before committing."
            ),
        }

        # If contracts section specifies assertions, merge them
        for c in contracts:
            for a in c.get("assertions", []):
                if isinstance(a, dict):
                    n = len(hard_assertions) + 1
                    hard_assertions.append({
                        "id": f"ASSERT-{n:02d}",
                        "description": a.get("description", a.get("id", "")),
                        "check_script": a.get("check", a.get("check_script", "npm test")),
                    })

        # Merge max_latency from requirements or meta
        max_latency = meta.get("max_latency_ms")
        if not max_latency:
            for r in requirements:
                c = r.get("constraint", {})
                if isinstance(c, dict) and "max-latency-ms" in c:
                    max_latency = c["max-latency-ms"]
                    break
        if max_latency:
            contract_data["constraints"]["max_latency_ms"] = int(max_latency)

        if contracts:
            contract_data["contract_id"] = contracts[0].get("name", "CNT-001")
            if contracts[0].get("version"):
                contract_data["version"] = contracts[0]["version"]

        return contract_data

    # ── Spec builder ───────────────────────────────────────

    def _build_spec(self, domain: str, modules: list, requirements: list,
                    invariants: list, tests: list, state_machines: list,
                    pipelines: list, meta: dict) -> str:
        """Build a current_spec.md from parsed DSL data."""
        lines = [f"# Spec — Generated from DSL ({domain})", ""]

        # Meta
        if meta.get("title"):
            lines.append(f"**Project**: {meta['title']}")
            lines.append("")

        # Modules
        lines.append("## Modules")
        for m in modules:
            desc = m.get("description", "")
            lang = m.get("language", "")
            extra = f" ({lang})" if lang else ""
            lines.append(f"- **{m['name']}**{extra}: {desc}")
        lines.append("")

        # Requirements
        if requirements:
            lines.append("## Requirements")
            for r in requirements:
                title = r.get("title", "")
                if isinstance(title, dict):
                    title = title.get("title", str(title)[:80])
                title = str(title) if title else ""
                priority = r.get("priority", "")
                if isinstance(priority, dict):
                    priority = ""
                priority_mark = f" `[{str(priority).upper()}]`" if priority and str(priority) else ""
                lines.append(f"### {r.get('id', '')}: {title}{priority_mark}")
                constraint = r.get("constraint", {})
                if isinstance(constraint, dict):
                    for k, v in constraint.items():
                        lines.append(f"- **{k}**: {v}")
                lines.append("")

        # Invariants
        if invariants:
            lines.append("## Invariants")
            for inv in invariants:
                desc = inv.get("description", "")
                enf = inv.get("enforcement", "hard")
                formula = inv.get("formula", "")
                lines.append(f"- **{inv.get('id', '')}** [{enf}]: {desc}")
                if formula:
                    lines.append(f"  - Formula: `{formula}`")
                check = inv.get("check", inv.get("check_script", ""))
                if check:
                    lines.append(f"  - Check: `{check}`")
            lines.append("")

        # Tests
        if tests:
            lines.append("## Tests")
            for t in tests:
                lines.append(f"### {t.get('id', '')}")
                covers = t.get("covers", "")
                if covers:
                    lines.append(f"- Covers: {covers}")
                given = t.get("given", "")
                when = t.get("when", "")
                then = t.get("then", "")
                if given:
                    lines.append(f"- Given: {given}")
                if when:
                    lines.append(f"- When: {when}")
                if then:
                    lines.append(f"- Then: {then}")
                lines.append("")

        # State machines
        if state_machines:
            lines.append("## State Machines")
            for sm in state_machines:
                lines.append(f"### {sm.get('name', '')}")
                states = sm.get("states", [])
                transitions = sm.get("transitions", [])
                if states:
                    lines.append(f"- States: {', '.join(s) if isinstance(states, list) else states}")
                if transitions:
                    lines.append("- Transitions:")
                    for tr in (transitions if isinstance(transitions, list) else [transitions]):
                        if isinstance(tr, dict):
                            lines.append(f"  - {tr.get('from','?')} → {tr.get('to','?')} [{tr.get('trigger','')}]")
                        elif isinstance(tr, str):
                            lines.append(f"  - {tr}")
                lines.append("")

        # Pipelines
        if pipelines:
            lines.append("## Data Pipelines")
            for p in pipelines:
                lines.append(f"### {p.get('name', '')}")
                source = p.get("source", {})
                sink = p.get("sink", {})
                transforms = p.get("transform", [])
                if isinstance(source, dict):
                    lines.append(f"- Source: {source.get('type','')} ({source.get('topic','')})")
                if transforms:
                    lines.append("- Transforms:")
                    for t in (transforms if isinstance(transforms, list) else [transforms]):
                        if isinstance(t, dict):
                            lines.append(f"  - {t.get('step','')}: {t.get('description','')}")
                if isinstance(sink, dict):
                    lines.append(f"- Sink: {sink.get('type','')} ({sink.get('table','')})")
                lines.append("")

        return "\n".join(lines)
