#!/usr/bin/env python3
"""
clada — Unified CLI entry point for CLADA system
Usage: python3 -m clada [command] [args]
"""
import sys
from pathlib import Path


def print_help():
    print("""
CLADA — Closed-Loop Autonomous Development Architecture
Phase 1: PTY + State Machine + Validator

USAGE:
  python3 -m clada                    Start interactive Gateway (main mode)
  python3 -m clada init               Run Bootstrap (create project constitution)
  python3 -m clada status             Show current system state
  python3 -m clada validate contract  Validate docs/spec/contract.json
  python3 -m clada validate dr <file> Validate a DR-xxx.md file
  python3 -m clada validate all       Validate all DRs in docs/decisions/
  python3 -m clada index rebuild      Rebuild L2 index.json
  python3 -m clada cold-start         Scan repo and generate architecture.md
  python3 -m clada help               Show this help

QUICK START:
  1. python3 -m clada init            # Bootstrap: define Goal + Contract
  2. python3 -m clada                 # Start Gateway
  3. clada> /propose <task>           # Describe your task
  4. clada> /execute                  # Start Executor
  5. clada> /merge                    # Merge after audit passes

REQUIREMENTS (macOS):
  pip install rich psutil jsonschema pexpect watchdog
  brew install fswatch   # optional, for file write monitoring
  npm install -g @anthropic-ai/claude-code   # Executor agent
""")


def cmd_status():
    from clada.orchestrator import RuntimeState, show_status
    runtime = RuntimeState()
    show_status(runtime)


def cmd_validate(args: list):
    import json
    from pathlib import Path
    from clada.contract_validator import (
        ContractValidator, DRValidator, L2IndexBuilder,
        print_validation_report
    )

    if not args:
        print("Usage: clada validate [contract|dr <file>|all]")
        return

    sub = args[0]
    root = Path(".")

    if sub == "contract":
        p = root / "docs" / "spec" / "contract.json"
        if not p.exists():
            print(f"Not found: {p}")
            sys.exit(1)
        try:
            contract = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
            sys.exit(1)
        v = ContractValidator()
        result = v.validate(contract)
        print_validation_report(result, "contract.json")
        sys.exit(0 if result.valid else 1)

    elif sub == "dr":
        if len(args) < 2:
            print("Usage: clada validate dr <path/to/DR-xxx.md>")
            sys.exit(1)
        p = Path(args[1])
        v = DRValidator()
        result = v.validate_file(p)
        print_validation_report(result, p.name)
        sys.exit(0 if result.valid else 1)

    elif sub == "all":
        d = root / "docs" / "decisions"
        v = DRValidator()
        summary = v.validate_index(d)
        print(f"\nValidated {summary['total']} DR files: "
              f"{summary['valid_count']} valid, "
              f"{summary['total'] - summary['valid_count']} invalid\n")
        for fname, r in summary["results"].items():
            status = "✅" if r["valid"] else "❌"
            print(f"  {status} {fname}")
            for e in r["errors"]:
                print(f"      ERROR [{e['field']}]: {e['message']}")
        sys.exit(0 if summary["valid_count"] == summary["total"] else 1)

    else:
        print(f"Unknown validate subcommand: {sub}")
        sys.exit(1)


def cmd_index_rebuild():
    from clada.contract_validator import L2IndexBuilder
    d = Path("docs/decisions")
    if not d.exists():
        print("docs/decisions/ not found — run 'clada init' first")
        sys.exit(1)
    builder = L2IndexBuilder(d)
    index = builder.rebuild()
    print(f"✅ Rebuilt: {index['total']} decisions, "
          f"{sum(1 for d in index['decisions'] if d.get('status') != 'superseded')} active")


def main():
    args = sys.argv[1:]

    if not args:
        from clada.orchestrator import main as orchestrator_main
        orchestrator_main()
        return

    cmd = args[0].lower()

    if cmd in ("help", "--help", "-h"):
        print_help()

    elif cmd == "init":
        from clada.bootstrap import run_bootstrap
        from clada.orchestrator import RuntimeState, FileAccessProxy
        runtime = RuntimeState()
        proxy = FileAccessProxy(Path("."), runtime)
        run_bootstrap(runtime, proxy)

    elif cmd == "status":
        cmd_status()

    elif cmd == "validate":
        cmd_validate(args[1:])

    elif cmd == "index":
        if len(args) > 1 and args[1] == "rebuild":
            cmd_index_rebuild()
        else:
            print("Usage: clada index rebuild")

    elif cmd == "cold-start":
        from clada.bootstrap import MemoryManager
        mm = MemoryManager()
        mm.scan_repo_for_architecture()

    else:
        print(f"Unknown command: {cmd}")
        print("Run 'python3 -m clada help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
