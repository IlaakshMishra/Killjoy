import re
import subprocess
import tomllib
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=300)


def _check_pyproject_mutmut_conflict(repo_path: Path) -> str | None:
    """Return an error message if the target repo's pyproject.toml already
    configures mutmut via a [tool.mutmut] table -- mutmut's config reader
    checks pyproject.toml for that table BEFORE reading setup.cfg, so our
    generated setup.cfg would be silently ignored if it's present."""
    pyproject_path = repo_path / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        data = tomllib.loads(pyproject_path.read_text())
    except tomllib.TOMLDecodeError:
        return None
    if "mutmut" in data.get("tool", {}):
        return (
            "target repo already configures mutmut via pyproject.toml; "
            "Evaluator's setup.cfg would be silently ignored"
        )
    return None


def run_mutation(repo_path: Path, touched_paths: list[str], test_file_rel_path: str) -> dict:
    conflict = _check_pyproject_mutmut_conflict(repo_path)
    if conflict:
        return {"error": conflict}

    # setup.cfg continuation-line format: a single value per line, indented
    # continuation lines for additional entries. configparser joins these
    # with "\n", and mutmut's list-valued config options split on "\n".
    paths_arg = "\n    ".join(touched_paths)

    setup_cfg = repo_path / "setup.cfg"
    setup_cfg.write_text(
        "[mutmut]\n"
        f"source_paths={paths_arg}\n"
        f"pytest_add_cli_args_test_selection={test_file_rel_path}\n"
    )

    run_result = _run(["mutmut", "run"], repo_path)

    results_result = _run(["mutmut", "results"], repo_path)
    results_output = results_result.stdout

    # mutmut 3.x prints one mutant per line as "<qualified.mutant_id>: survived"
    # (e.g. "    target.x_add_bonus__mutmut_1: survived"), not the older
    # "<numeric_id>. ... survived" format the first regex below assumes.
    survived_ids = re.findall(r"^\s*(\S+):\s*survived", results_output, re.MULTILINE | re.IGNORECASE)
    if not survived_ids:
        survived_ids = re.findall(r"^(\d+)\.\s.*survived", results_output, re.MULTILINE | re.IGNORECASE)
    if not survived_ids:
        survived_ids = [
            line.split(":")[0].strip()
            for line in results_output.splitlines()
            if "survived" in line.lower()
        ]

    killed_match = re.search(r"(\d+)/(\d+)", run_result.stdout)
    total_mutants = int(killed_match.group(2)) if killed_match else (len(survived_ids))
    if total_mutants == 0:
        return {"error": "mutation testing produced no parseable mutant results (total_mutants=0)"}
    survived_count = len(survived_ids)
    killed_count = max(total_mutants - survived_count, 0)

    surviving_mutants = []
    for mutant_id in survived_ids:
        show_result = _run(["mutmut", "show", mutant_id], repo_path)
        diff_text = show_result.stdout
        line_match = re.search(r"@@ -(\d+)", diff_text)
        line_number = int(line_match.group(1)) if line_match else 0
        target_file = touched_paths[0] if touched_paths else ""
        surviving_mutants.append({
            "id": mutant_id,
            "file": target_file,
            "line": line_number,
            "description": diff_text.strip()[:500],
        })

    score = (killed_count / total_mutants) if total_mutants else 1.0

    return {
        "score": round(score, 4),
        "killed": killed_count,
        "survived": survived_count,
        "surviving_mutants": surviving_mutants,
    }
