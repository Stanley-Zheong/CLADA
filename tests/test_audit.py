import json

from clada.audit import generate_audit_report
from clada.checkpoint import CheckpointDiff


def test_audit_report_contains_summary_and_path_scoped_rollback(tmp_path):
    log_path = tmp_path / "sess.jsonl"
    events = [
        {"event": "command", "session_id": "sess-1", "argv": ["echo", "ok"]},
        {"event": "policy_enforced", "session_id": "sess-1", "mode": "enforced"},
        {
            "event": "session_end",
            "session_id": "sess-1",
            "exit_status": 0,
            "ok": True,
            "duration_seconds": 0.1,
        },
    ]
    log_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    diff = CheckpointDiff(
        root=tmp_path,
        git_available=True,
        session_owned_paths=["src/new.py"],
        pre_existing_dirty_paths=["README.md"],
        resolved_pre_existing_paths=[],
    )

    report = generate_audit_report(
        session_id="sess-1",
        log_path=log_path,
        checkpoint=diff,
        audits_dir=tmp_path / "audits",
    )
    text = report.read_text(encoding="utf-8")

    assert "CLADA Session Audit: sess-1" in text
    assert "`src/new.py`" in text
    assert "`README.md`" in text
    assert "git restore -- `src/new.py`" not in text
    assert "git restore -- 'src/new.py'" in text
    assert "file contents" in text
