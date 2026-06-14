"""
Tests for ContractValidator and DRValidator.

These validators are pure (ContractValidator takes a dict; DRValidator reads a
file path), so the only filesystem interaction is reading DR files written into
pytest's tmp_path by the fixtures in conftest.py.
"""

from clada.contract_validator import ContractValidator, DRValidator


# ── ContractValidator ──────────────────────────────────────────────

def test_valid_contract_passes(valid_contract):
    result = ContractValidator().validate(valid_contract)
    assert result.valid is True
    assert result.errors == []


def test_invalid_contract_fails(invalid_contract):
    result = ContractValidator().validate(invalid_contract)
    assert result.valid is False
    assert len(result.errors) >= 1


def test_duplicate_assertion_ids_rejected(valid_contract):
    valid_contract["hard_assertions"].append({
        "id": "ASSERT-01",  # duplicate of the existing one
        "description": "Another check",
        "check_script": "pytest",
    })
    result = ContractValidator().validate(valid_contract)
    assert result.valid is False
    assert any("unique" in e["message"].lower() for e in result.errors)


def test_missing_latency_emits_warning(valid_contract):
    valid_contract["constraints"].pop("max_latency_ms", None)
    result = ContractValidator().validate(valid_contract)
    assert result.valid is True  # warning, not an error
    assert any("latency" in w["field"].lower() or "latency" in w["message"].lower()
               for w in result.warnings)


# ── DRValidator ────────────────────────────────────────────────────

def test_valid_dr_file_passes(valid_dr_file):
    result = DRValidator().validate_file(valid_dr_file)
    assert result.valid is True
    assert result.errors == []


def test_invalid_dr_file_fails(invalid_dr_file):
    result = DRValidator().validate_file(invalid_dr_file)
    assert result.valid is False
    fields = {e["field"] for e in result.errors}
    # missing title, malformed id, bad status and bad date are all flagged
    assert "frontmatter.title" in fields
    assert "frontmatter.id" in fields
    assert "frontmatter.status" in fields
    assert "frontmatter.date" in fields


def test_missing_dr_file_reports_error(tmp_path):
    result = DRValidator().validate_file(tmp_path / "does-not-exist.md")
    assert result.valid is False
    assert any(e["field"] == "file" for e in result.errors)


def test_validate_index_summarizes_directory(valid_dr_file, invalid_dr_file, tmp_path):
    # Place one valid and one invalid DR in a dedicated directory.
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    (decisions / "DR-001.md").write_text(valid_dr_file.read_text(), encoding="utf-8")
    (decisions / "DR-002.md").write_text(invalid_dr_file.read_text(), encoding="utf-8")

    summary = DRValidator().validate_index(decisions)
    assert summary["total"] == 2
    assert summary["valid_count"] == 1
