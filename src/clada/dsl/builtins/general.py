"""
General software development DSL (default domain).
Suitable for web apps, APIs, and general backend services.
"""

GENERAL_DOMAIN = {
    "name": "general",
    "description": "General software development — web apps, APIs, backend services",
    "keywords": ["module", "requirement", "invariant", "test", "contract", "state-machine"],
    "phases": {
        "bootstrap": {
            "description": "Initial project setup",
            "required_fields": ["modules", "requirements"],
            "template": """(domain general)
(meta
  title: "<project-name>"
  description: "<brief>"
  language: "<lang>"
  framework: "<framework>")

(module core
  description: "<description>"
  language: "<lang>")

(requirement REQ-001
  title: "<title>"
  priority: high)

(invariant INV-01
  description: "<description>"
  enforcement: hard
  check: "<check_script>")
""",
        },
    },
}
