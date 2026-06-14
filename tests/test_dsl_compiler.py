"""
Tests for the DSL compiler (DSLCompiler.compile / compile_file).

Covers the happy path (a valid S-expression project description compiles into a
contract.json dict + spec.md) and the documented failure modes (empty source
and source with no forms).
"""

from clada.contract_validator import ContractValidator
from clada.dsl.compiler import DSLCompiler


# ── Happy path ─────────────────────────────────────────────────────

def test_valid_dsl_compiles(valid_dsl):
    result = DSLCompiler().compile(valid_dsl)
    assert result.success is True
    assert result.errors == []
    assert result.domain == "general"


def test_compiled_contract_shape(valid_dsl):
    result = DSLCompiler().compile(valid_dsl)
    contract = result.contract
    assert contract["contract_id"].startswith("CNT-")
    assert "core" in contract["scope"]["modules"]
    assert len(contract["hard_assertions"]) >= 1
    # The invariant marked enforcement: hard becomes a hard assertion.
    descriptions = [a["description"] for a in contract["hard_assertions"]]
    assert any("plaintext" in d.lower() for d in descriptions)


def test_compiled_spec_contains_sections(valid_dsl):
    result = DSLCompiler().compile(valid_dsl)
    assert "## Modules" in result.spec
    assert "## Requirements" in result.spec
    assert "REQ-01" in result.spec


def test_compiled_contract_is_schema_valid(valid_dsl):
    """Cross-check: DSL output must satisfy the contract meta-schema."""
    result = DSLCompiler().compile(valid_dsl)
    validation = ContractValidator().validate(result.contract)
    assert validation.valid is True, validation.errors


def test_compile_file_roundtrip(valid_dsl, tmp_path):
    dsl_path = tmp_path / "project.dsl"
    dsl_path.write_text(valid_dsl, encoding="utf-8")
    result = DSLCompiler().compile_file(dsl_path)
    assert result.success is True

    # write_to must land artifacts under the given temp dirs, not the repo.
    spec_dir = tmp_path / "spec"
    decisions_dir = tmp_path / "decisions"
    contract_path, spec_path = result.write_to(spec_dir, decisions_dir)
    assert contract_path.exists()
    assert spec_path.exists()


# ── Failure modes ──────────────────────────────────────────────────

def test_empty_source_fails(empty_dsl):
    result = DSLCompiler().compile(empty_dsl)
    assert result.success is False
    assert any("empty" in e.lower() for e in result.errors)


def test_comment_only_source_fails(comment_only_dsl):
    # A source with only comments parses to zero forms → same failure path.
    result = DSLCompiler().compile(comment_only_dsl)
    assert result.success is False
    assert result.errors
