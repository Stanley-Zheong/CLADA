"""
Shared pytest fixtures for the CLADA baseline test suite.

Fixtures provide ready-made inputs for the three things Phase 1 tests:
  - contracts        → valid/invalid contract.json dicts
  - DR files         → valid/invalid DR-xxx.md files on a temp path
  - DSL input        → valid/invalid DSL source strings

All filesystem fixtures use pytest's ``tmp_path`` so the real repository
runtime state (runtime/, docs/, .comm/) is never touched.
"""

import pytest


# ── Contract fixtures ──────────────────────────────────────────────

@pytest.fixture
def valid_contract() -> dict:
    """A contract that satisfies the CLADA meta-schema."""
    return {
        "contract_id": "CNT-001",
        "version": "1.0.0",
        "scope": {
            "modules": ["core"],
            "interfaces": ["POST /api/v1/main"],
        },
        "constraints": {
            "strict_types": True,
            "max_latency_ms": 200,
            "allowed_dependencies": ["rich"],
        },
        "hard_assertions": [
            {
                "id": "ASSERT-01",
                "description": "All tests must pass",
                "check_script": "pytest",
            }
        ],
    }


@pytest.fixture
def invalid_contract() -> dict:
    """A contract that violates several hard schema rules."""
    return {
        "contract_id": "BAD-ID",          # wrong prefix/format
        "version": "1.0",                 # not semantic x.y.z
        "scope": {"modules": [], "interfaces": []},  # empty arrays
        "constraints": {},                # missing strict_types/allowed_dependencies
        "hard_assertions": [],            # empty (schema requires items via manual path)
    }


# ── DR file fixtures ───────────────────────────────────────────────

VALID_DR = """\
---
id: DR-001
title: Adopt a finite state machine for the Gateway
status: accepted
date: 2026-06-14
---

## Context
The Gateway must enforce an eight-state lifecycle with explicit, gated
transitions so that no invalid state change can ever occur.

## Decision
We will model the lifecycle as an explicit finite state machine with a
whitelist of valid transitions.

## Trade-offs
Pros: predictable, auditable transitions and trivial invalid-transition
rejection. Cons: more boilerplate when adding new states. The clarity is
worth the extra verbosity for a safety-critical controller.
"""

INVALID_DR = """\
---
id: DR-1
status: unknown
date: not-a-date
---

## Context
Missing title, malformed id, bad status and bad date on purpose.
"""


@pytest.fixture
def valid_dr_file(tmp_path):
    p = tmp_path / "DR-001.md"
    p.write_text(VALID_DR, encoding="utf-8")
    return p


@pytest.fixture
def invalid_dr_file(tmp_path):
    # Distinct filename from valid_dr_file so both can coexist in one tmp_path.
    p = tmp_path / "DR-002.md"
    p.write_text(INVALID_DR, encoding="utf-8")
    return p


# ── DSL fixtures ───────────────────────────────────────────────────

VALID_DSL = """\
;; A minimal but complete CLADA DSL project description
(domain general)
(module core "core domain logic" language: python)
(requirement REQ-01 "User can log in" priority: high)
(invariant INV-01 "Passwords are never stored in plaintext"
           enforcement: hard check: "pytest tests/security")
(test T-01 covers: REQ-01)
"""

EMPTY_DSL = ""

COMMENT_ONLY_DSL = ";; nothing but a comment, no forms\n"


@pytest.fixture
def valid_dsl() -> str:
    return VALID_DSL


@pytest.fixture
def empty_dsl() -> str:
    return EMPTY_DSL


@pytest.fixture
def comment_only_dsl() -> str:
    return COMMENT_ONLY_DSL
