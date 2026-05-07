"""
DSL Domain Registry — maps domain names to their schemas and validators.
Built-in domains: general, fintech, saas, data_pipeline.
"""

from typing import Optional

from clada.dsl.builtins.general import GENERAL_DOMAIN
from clada.dsl.builtins.fintech import FINTECH_DOMAIN
from clada.dsl.builtins.saas import SAAS_DOMAIN
from clada.dsl.builtins.data_pipeline import DATA_PIPELINE_DOMAIN

BUILTIN_DOMAINS = {
    "general/web": GENERAL_DOMAIN,
    "general": GENERAL_DOMAIN,
    "fintech/payment": FINTECH_DOMAIN,
    "fintech": FINTECH_DOMAIN,
    "saas/web": SAAS_DOMAIN,
    "saas": SAAS_DOMAIN,
    "data/etl": DATA_PIPELINE_DOMAIN,
    "data_pipeline": DATA_PIPELINE_DOMAIN,
}

# User-defined domains can be registered at runtime
_custom_domains: dict[str, dict] = {}


class DSLRegistry:

    @staticmethod
    def get(domain_name: str) -> Optional[dict]:
        """Get a domain definition by name."""
        domain = _custom_domains.get(domain_name)
        if domain is None:
            domain = BUILTIN_DOMAINS.get(domain_name)
        return domain

    @staticmethod
    def register(name: str, definition: dict):
        """Register a custom domain DSL."""
        _custom_domains[name] = definition

    @staticmethod
    def list_all() -> list[str]:
        """List all registered domain names."""
        return sorted(set(BUILTIN_DOMAINS) | set(_custom_domains))


def list_domains() -> list[str]:
    return DSLRegistry.list_all()
