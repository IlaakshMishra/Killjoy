import subprocess
import traceback
from pathlib import Path
import pytest

from git_ops import clone_repo, create_branch_and_commit, push_branch


def _init_local_repo_with_remote(tmp_path: Path) -> Path:
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_path)], check=True)

    work_path = tmp_path / "work"
    work_path.mkdir()
    subprocess.run(["git", "init"], cwd=work_path, check=True)
    subprocess.run(["git", "config", "user.email", "killjoy@example.com"], cwd=work_path, check=True)
    subprocess.run(["git", "config", "user.name", "Killjoy"], cwd=work_path, check=True)
    (work_path / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=work_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=work_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=work_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_path)], cwd=work_path, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=work_path, check=True)

    return work_path


def test_create_branch_and_commit_writes_files_and_commits(tmp_path):
    work_path = _init_local_repo_with_remote(tmp_path)

    create_branch_and_commit(
        work_path,
        branch_name="killjoy/pr-1-abc123",
        files={"tests/killjoy/generated/test_new.py": "def test_new():\n    assert True\n"},
        commit_message="test: add generated integration test",
    )

    result = subprocess.run(["git", "branch", "--show-current"], cwd=work_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "killjoy/pr-1-abc123"

    assert (work_path / "tests/killjoy/generated/test_new.py").exists()

    log_result = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=work_path, capture_output=True, text=True, check=True)
    assert log_result.stdout.strip() == "test: add generated integration test"


def test_push_branch_pushes_to_remote(tmp_path):
    work_path = _init_local_repo_with_remote(tmp_path)
    create_branch_and_commit(
        work_path,
        branch_name="killjoy/pr-2-def456",
        files={"tests/killjoy/generated/test_new.py": "def test_new():\n    assert True\n"},
        commit_message="test: add generated integration test",
    )

    push_branch(work_path, "killjoy/pr-2-def456")

    remote_path = tmp_path / "remote.git"
    ls_remote = subprocess.run(
        ["git", "ls-remote", "--heads", str(remote_path), "killjoy/pr-2-def456"],
        capture_output=True, text=True, check=True,
    )
    assert "killjoy/pr-2-def456" in ls_remote.stdout


def test_clone_repo_does_not_leak_token_on_failure(tmp_path):
    """Verify that the token is redacted from error messages to prevent leaks in logs."""
    test_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    nonexistent_url = "https://example.com/nonexistent/repo.git"
    dest_path = tmp_path / "cloned"

    with pytest.raises(RuntimeError) as exc_info:
        clone_repo(nonexistent_url, test_token, dest_path, "main")

    # Verify the token does not appear in the exception message
    assert test_token not in str(exc_info.value)

    # Verify the token does not appear in the full formatted traceback (including exception chain)
    formatted_traceback = "".join(traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.tb))
    assert test_token not in formatted_traceback
