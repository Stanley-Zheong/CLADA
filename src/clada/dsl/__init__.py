"""
CLADA DSL Module — Domain-Specific Language for project description.
S-expression based; compiles to contract.json + spec.md.
"""

from clada.dsl.parser import parse_file, parse_string, SExpr
from clada.dsl.compiler import DSLCompiler, CompileResult
from clada.dsl.registry import DSLRegistry, list_domains
