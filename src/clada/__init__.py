"""
CLADA — Closed-Loop Autonomous Development Architecture
Phase 1: PTY + State Machine + Validator
"""

from clada.orchestrator import State, RuntimeState, FileAccessProxy, show_status, main as orchestrator_main
from clada.bootstrap import run_bootstrap, MemoryManager
from clada.contract_validator import (
    ContractValidator, DualLockComparator,
    DRValidator, L2IndexBuilder,
    print_validation_report,
)

__version__ = "1.0.0"
