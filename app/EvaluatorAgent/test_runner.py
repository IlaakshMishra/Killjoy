from pathlib import Path
from runner import run_pytest


def test_run_pytest_reports_pass_for_passing_test(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 == 1\n")

    result = run_pytest(tmp_path, "test_ok.py")

    assert result["passed"] is True
    assert result["returncode"] == 0


def test_run_pytest_reports_failure_for_failing_test(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert 1 == 2\n")

    result = run_pytest(tmp_path, "test_bad.py")

    assert result["passed"] is False
    assert result["returncode"] != 0
    assert "1 failed" in result["summary"] or "failed" in result["summary"].lower()


def test_run_pytest_handles_timeout(tmp_path):
    (tmp_path / "test_slow.py").write_text("import time\ndef test_slow():\n    time.sleep(2)\n    assert 1 == 1\n")

    result = run_pytest(tmp_path, "test_slow.py", timeout=1)

    assert result["passed"] is False
    assert result["returncode"] == -1
    assert "timed out" in result["summary"].lower()
