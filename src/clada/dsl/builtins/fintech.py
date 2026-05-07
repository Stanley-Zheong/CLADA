"""
Fintech / payment systems DSL.
Adds compliance, state machines for transactions, and financial invariants.
"""

FINTECH_DOMAIN = {
    "name": "fintech",
    "description": "Financial technology — payments, settlements, compliance-heavy systems",
    "keywords": ["module", "requirement", "invariant", "test", "contract",
                  "state-machine", "compliance", "audit-trail"],
    "extra_invariant_types": {
        "ledger_balance": "借贷恒等: (sum credits) == (sum debits)",
        "dual_control": "关键操作需要双重审批",
        "audit_trail": "所有操作必须有完整审计追踪",
    },
    "phases": {
        "bootstrap": {
            "description": "Financial system bootstrap with compliance",
            "required_fields": ["modules", "requirements", "compliance"],
            "template": """(domain fintech/payment)
(meta
  title: "<project-name>"
  compliance: "<PCI-DSS>" "<SOX>" "<local-regulation>")

(module settlement
  description: "<description>"
  language: "<lang>")

(requirement REQ-001
  title: "<title>"
  priority: critical
  constraint
    correctness: "exactly-once"
    auditability: "full-trace")

(invariant INV-MONEY-01
  description: "借贷恒等"
  enforcement: hard
  formula: "(sum credits) == (sum debits)")
""",
        },
    },
}
