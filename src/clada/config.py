"""
CLADA LLM Configuration Module
Loads .clada/config.yml with ${ENV_VAR} substitution.
Provides typed access to per-role model configurations.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

CLADA_ROOT = Path(__file__).parent.parent.parent
DEFAULT_CONFIG_PATH = CLADA_ROOT / ".clada" / "config.yml"

_ENV_VAR_RE = re.compile(r'\$\{([^}]+)\}')


def _subst_env(value: str) -> str:
    """Replace ${VAR} patterns with environment variable values."""
    def replacer(m):
        var = m.group(1)
        return os.environ.get(var, f"<MISSING:{var}>")
    return _ENV_VAR_RE.sub(replacer, value)


def _deep_subst(obj):
    """Recursively substitute env vars in strings."""
    if isinstance(obj, str):
        return _subst_env(obj)
    elif isinstance(obj, dict):
        return {k: _deep_subst(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_subst(v) for v in obj]
    return obj


@dataclass
class ProviderConfig:
    """Connection info for a single LLM provider."""
    name: str
    api_key: str = ""
    base_url: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "ProviderConfig":
        return cls(
            name=name,
            api_key=data.get("api_key", ""),
            base_url=data.get("base_url", ""),
        )


@dataclass
class RoleConfig:
    """Model configuration for a single CLADA role."""
    role: str
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    mode: str = "cli"          # "cli" or "api"
    cli_path: str = "claude"
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, role: str, data: dict, defaults: dict) -> "RoleConfig":
        merged = {**defaults, **data}
        return cls(
            role=role,
            provider=data.get("provider", "anthropic"),
            model=data.get("model", "claude-sonnet-4-6"),
            mode=data.get("mode", "cli"),
            cli_path=data.get("cli_path", "claude"),
            params={
                "temperature": merged.get("temperature", 0.3),
                "max_tokens": merged.get("max_tokens", 16000),
                **data.get("params", {}),
            },
        )


@dataclass
class CLADAConfig:
    """
    Top-level CLADA configuration loaded from .clada/config.yml.
    Falls back to sensible defaults if config file is absent.
    """
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: dict[str, RoleConfig] = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    _loaded: bool = False

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CLADAConfig":
        config_path = path or find_config()
        config = cls()

        if config_path and config_path.exists():
            config._loaded = True
            try:
                raw_text = config_path.read_text(encoding="utf-8")
                if HAS_YAML:
                    raw = yaml.safe_load(raw_text) or {}
                else:
                    raw = _parse_yaml_simple(raw_text)
                raw = _deep_subst(raw)

                # Parse providers
                for name, data in raw.get("providers", {}).items():
                    config.providers[name] = ProviderConfig.from_dict(name, data)

                # Parse defaults
                config.defaults = raw.get("defaults", {})

                # Parse roles
                for role_name, role_data in raw.get("roles", {}).items():
                    config.roles[role_name] = RoleConfig.from_dict(
                        role_name, role_data, config.defaults
                    )
            except Exception as e:
                import sys
                print(f"[CONFIG] Warning: failed to parse {config_path}: {e}", file=sys.stderr)
                config._loaded = False

        return config

    def get_role(self, role: str) -> RoleConfig:
        """Get config for a named role, or sensible default."""
        if role in self.roles:
            return self.roles[role]
        defaults = self.defaults
        return RoleConfig(role=role, params={
            "temperature": defaults.get("temperature", 0.3),
            "max_tokens": defaults.get("max_tokens", 16000),
        })

    def get_executor(self) -> RoleConfig:
        return self.get_role("executor")

    def get_verifier(self) -> RoleConfig:
        return self.get_role("verifier")

    def get_bootstrap_pair(self) -> tuple[RoleConfig, RoleConfig]:
        return self.get_role("bootstrap_a"), self.get_role("bootstrap_b")

    def get_provider(self, name: str) -> Optional[ProviderConfig]:
        return self.providers.get(name)


def find_config() -> Optional[Path]:
    """Walk up from current directory to find .clada/config.yml."""
    start = Path.cwd()
    for parent in [start] + list(start.parents):
        candidate = parent / ".clada" / "config.yml"
        if candidate.exists():
            return candidate
    # Fallback: check default location in project root
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return None


def _parse_yaml_simple(text: str) -> dict:
    """
    Minimal YAML parser for the common case when PyYAML is not installed.
    Handles nested dicts, lists, and simple values.
    """
    result = {}
    stack = [(result, -1)]  # (dict, indent)

    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        key, _, val = stripped.partition(":")
        key = key.strip().strip('"\'')
        val = val.strip()

        # Pop stack until we find the right parent
        while stack and stack[-1][1] >= indent:
            stack.pop()
        current_dict, _ = stack[-1]

        if val == "" or val == "{}":
            # Nested dict start
            current_dict[key] = {}
            stack.append((current_dict[key], indent))
        elif val.startswith("[") and val.endswith("]"):
            # List
            items = [v.strip().strip('"\'') for v in val[1:-1].split(",") if v.strip()]
            current_dict[key] = items
        elif val == "true":
            current_dict[key] = True
        elif val == "false":
            current_dict[key] = False
        elif val.replace(".", "").isdigit():
            current_dict[key] = float(val) if "." in val else int(val)
        else:
            current_dict[key] = val.strip('"\'').strip()

    return result


def create_default_config(path: Optional[Path] = None) -> Path:
    """Create a default .clada/config.yml template."""
    target = path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return target


DEFAULT_CONFIG_TEMPLATE = """# CLADA LLM Configuration
# Environment variables use ${VAR} syntax for safety.

providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    base_url: https://api.anthropic.com

  openai:
    api_key: ${OPENAI_API_KEY}
    base_url: https://api.openai.com/v1

  ollama:
    base_url: http://localhost:11434

roles:
  executor:
    provider: anthropic
    model: claude-opus-4-7
    mode: cli
    cli_path: claude
    params:
      thinking_budget: 16000
      max_tokens: 32000

  verifier:
    provider: anthropic
    model: claude-sonnet-4-6
    mode: api
    params:
      temperature: 0.1
      max_tokens: 8000

  bootstrap_a:
    provider: anthropic
    model: claude-opus-4-7
    mode: api

  bootstrap_b:
    provider: anthropic
    model: claude-sonnet-4-6
    mode: api

defaults:
  temperature: 0.3
  max_tokens: 16000
"""
