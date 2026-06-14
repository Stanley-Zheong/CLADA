import subprocess

from clada.checkpoint import SessionCheckpoint, stage_session_paths


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "owned.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "owned.txt")
    _git(tmp_path, "commit", "-m", "base")
    return tmp_path


def test_checkpoint_separates_pre_existing_dirty_from_session_changes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "owned.txt").write_text("owner dirty\n", encoding="utf-8")

    checkpoint = SessionCheckpoint.capture(repo)
    (repo / "session.txt").write_text("session change\n", encoding="utf-8")

    diff = checkpoint.diff()

    assert diff.pre_existing_dirty_paths == ["owned.txt"]
    assert diff.session_owned_paths == ["session.txt"]


def test_stage_session_paths_leaves_pre_existing_dirty_unstaged(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "owned.txt").write_text("owner dirty\n", encoding="utf-8")
    checkpoint = SessionCheckpoint.capture(repo)

    (repo / "session.txt").write_text("session change\n", encoding="utf-8")
    diff = checkpoint.diff()
    result = stage_session_paths(repo, diff.safe_stage_args())

    assert result.returncode == 0
    status = _git(repo, "status", "--porcelain=v1").stdout.splitlines()
    assert " M owned.txt" in status
    assert "A  session.txt" in status
    assert (repo / "owned.txt").read_text(encoding="utf-8") == "owner dirty\n"


def test_rollback_commands_are_directly_executable_for_untracked_session_files(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "owned.txt").write_text("owner dirty\n", encoding="utf-8")
    checkpoint = SessionCheckpoint.capture(repo)

    (repo / "session.txt").write_text("session change\n", encoding="utf-8")
    diff = checkpoint.diff()

    assert diff.rollback_commands() == ["git clean -f -- 'session.txt'"]
    result = subprocess.run(
        diff.rollback_commands()[0],
        cwd=str(repo),
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not (repo / "session.txt").exists()
    assert (repo / "owned.txt").read_text(encoding="utf-8") == "owner dirty\n"


def test_rollback_commands_are_path_scoped_for_tracked_session_files(tmp_path):
    repo = _init_repo(tmp_path)
    checkpoint = SessionCheckpoint.capture(repo)
    (repo / "owned.txt").write_text("session change\n", encoding="utf-8")
    diff = checkpoint.diff()

    assert diff.rollback_commands() == [
        "git restore --staged -- 'owned.txt'",
        "git restore -- 'owned.txt'",
    ]
