"""
SaaS / multi-tenant web application DSL.
Adds tenant isolation, rate limiting, and subscription-aware constraints.
"""

SAAS_DOMAIN = {
    "name": "saas",
    "description": "SaaS / multi-tenant web applications with tenant isolation and rate limiting",
    "keywords": ["module", "requirement", "invariant", "test", "contract",
                  "tenant", "rate-limit", "subscription", "entitlement"],
    "extra_invariant_types": {
        "tenant_isolation": "租户数据隔离: tenant-A data never accessible to tenant-B",
        "data_residency": "数据本地化: data stored in tenant's region",
        "rate_limit": "API 限流: per-tenant rate limits enforced",
    },
    "phases": {
        "bootstrap": {
            "description": "SaaS application bootstrap with multi-tenancy",
            "required_fields": ["modules", "requirements"],
            "template": """(domain saas/web)
(meta
  title: "<project-name>"
  multi_tenant: true)

(module tenant-service
  description: "<description>"
  language: "<lang>")

(requirement REQ-001
  title: "<title>"
  priority: high
  constraint
    tenant_isolation: "strict")

(invariant INV-TENANT-01
  description: "租户数据隔离"
  enforcement: hard
  check: "npm run test:tenant-isolation")
""",
        },
    },
}
