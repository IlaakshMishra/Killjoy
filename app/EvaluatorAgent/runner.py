import subprocess
from pathlib import Path


def run_pytest(repo_path: Path, test_file_rel_path: str, timeout: int = 120) -> dict:
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", test_file_rel_path, "-v"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        summary_lines = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line or "error" in line]
        summary = summary_lines[-1] if summary_lines else result.stdout[-500:]

        return {
            "passed": result.returncode == 0,
            "summary": summary,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "summary": f"test run timed out after {timeout}s",
            "returncode": -1,
        }
