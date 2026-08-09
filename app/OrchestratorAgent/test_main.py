import subprocess
import tempfile
import traceback
from pathlib import Path

import os

import pytest

os.environ.setdefault("ENV_MAPPER_ARN", "arn:aws:test:env-mapper")
os.environ.setdefault("TEST_GENERATOR_ARN", "arn:aws:test:test-generator")
os.environ.setdefault("EVALUATOR_ARN", "arn:aws:test:evaluator")
os.environ.setdefault("CI_PR_DELIVERER_ARN", "arn:aws:test:ci-pr-deliverer")
os.environ.setdefault("GITHUB_SECRET_ARN", "arn:aws:test:github-secret")

import importlib.util

_spec = importlib.util.spec_from_file_location("orchestrator_agent_main", Path(__file__).parent / "main.py")
main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main)
_clone_for_analysis = main._clone_for_analysis


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

    return remote_path


def _capture_mkdtemp(monkeypatch):
    """Patch tempfile.mkdtemp (as seen by main.py) to record the dest path it
    hands back, so tests can assert on cleanup after _clone_for_analysis raises."""
    captured = {}
    real_mkdtemp = tempfile.mkdtemp

    def fake_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        captured["path"] = path
        return path

    monkeypatch.setattr(main.tempfile, "mkdtemp", fake_mkdtemp)
    return captured


def test_clone_for_analysis_does_not_leak_token_on_failure():
    """Verify that the token is redacted from error messages (and the full
    exception chain/traceback) to prevent leaks in logs, mirroring the fix
    already proven in CIPRDelivererAgent/git_ops.py::clone_repo."""
    test_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    nonexistent_url = "https://example.com/nonexistent/repo.git"

    with pytest.raises(RuntimeError) as exc_info:
        _clone_for_analysis(nonexistent_url, test_token, "main")

    # Verify the token does not appear in the exception message
    assert test_token not in str(exc_info.value)

    # Verify the token does not appear in the full formatted traceback
    # (including the exception chain) -- `from None` must actually suppress
    # __cause__/__context__, not just the bare str() of the message.
    formatted_traceback = "".join(traceback.format_exception(type(exc_info.value), exc_info.value, exc_info.tb))
    assert test_token not in formatted_traceback


def test_clone_for_analysis_cleans_up_dest_when_clone_fails(monkeypatch):
    """Case 1: the git clone command itself fails. The mkdtemp-created dest
    directory must not be left behind on disk."""
    captured = _capture_mkdtemp(monkeypatch)
    test_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    nonexistent_url = "https://example.com/nonexistent/repo.git"

    with pytest.raises(RuntimeError):
        _clone_for_analysis(nonexistent_url, test_token, "main")

    assert "path" in captured, "mkdtemp was never called"
    assert not Path(captured["path"]).exists(), "dest directory leaked on clone failure"


def test_clone_for_analysis_cleans_up_dest_when_checkout_fails(tmp_path, monkeypatch):
    """Case 2 (the bigger leak): clone succeeds but the subsequent git checkout
    of a nonexistent ref fails. The dest directory -- now fully populated with
    the cloned repo -- must still be cleaned up before the exception propagates."""
    remote_path = _init_local_repo_with_remote(tmp_path)
    captured = _capture_mkdtemp(monkeypatch)

    with pytest.raises(RuntimeError):
        _clone_for_analysis(str(remote_path), "unused-token", "nonexistent-branch-xyz")

    assert "path" in captured, "mkdtemp was never called"
    assert not Path(captured["path"]).exists(), "dest directory leaked on checkout failure"


def test_handler_returns_structured_failure_when_clone_or_diff_raises(monkeypatch):
    """Reproduces the bug where reserve_run's irreversible dedup marker was
    committed BEFORE the clone/diff/read-files logic, which could raise a raw
    unhandled exception -- crashing the whole handler uncaught and locking out
    all future retries for that PR (reserve_run would keep saying "already
    ran"), instead of returning the structured {"status": "failed", ...} shape
    the rest of the system (pipeline.py's _failed()) uses.

    Injects a failure in _clone_for_analysis (post-reserve_run) and asserts
    the handler now returns a structured failed dict instead of raising."""
    import asyncio

    monkeypatch.setattr(main, "reserve_run", lambda *args, **kwargs: (True, None))
    monkeypatch.setattr(main, "_get_github_token", lambda: "fake-token")

    def fake_clone_raises(*args, **kwargs):
        raise RuntimeError("simulated clone failure")

    monkeypatch.setattr(main, "_clone_for_analysis", fake_clone_raises)

    payload = {
        "repo_full_name": "acme/widgets",
        "pr_number": 7,
        "repo_url": "https://example.com/acme/widgets.git",
        "head_ref": "feature-branch",
        "base_ref": "main",
    }

    result = asyncio.run(main.handler(payload))

    assert result == {
        "status": "failed",
        "stage_failed": "clone_or_diff",
        "reason": "simulated clone failure",
        "pr_url": None,
        "rounds": [],
        "final_test_source": None,
    }
