import tomllib
from typing import Callable


def _find_test_directory(repo_files: dict[str, str]) -> str:
    """Directory the generated test should be written into so pytest's
    conftest.py fixture discovery actually finds it. pytest only looks at
    conftest.py in a test file's own directory and its ancestors, not
    sibling directories -- placing the test at repo root while conftest.py
    lives in e.g. "tests/" makes every conftest-dependent fixture
    ("fixture 'x' not found") invisible. The deepest conftest.py's
    directory is used since a test placed there can still see every
    shallower ancestor conftest.py too, but not vice versa.
    """
    conftest_paths = [p for p in repo_files if p.endswith("conftest.py")]
    if not conftest_paths:
        return ""
    deepest = max(conftest_paths, key=lambda p: p.count("/"))
    return deepest.rsplit("/", 1)[0] if "/" in deepest else ""


def _check_pyproject_mutmut_conflict(repo_files: dict[str, str]) -> str | None:
    """Return an error message if the target repo's pyproject.toml already
    configures mutmut via a [tool.mutmut] table -- mutmut's config reader
    checks pyproject.toml for that table BEFORE reading setup.cfg, so our
    generated setup.cfg would be silently ignored if it's present."""
    pyproject_text = repo_files.get("pyproject.toml")
    if not pyproject_text:
        return None
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError:
        return None
    if "mutmut" in data.get("tool", {}):
        return (
            "target repo already configures mutmut via pyproject.toml; "
            "Evaluator's setup.cfg would be silently ignored"
        )
    return None


def execute_in_sandbox(
    start_fn: Callable[[], str],
    invoke_fn: Callable[[str, str, dict], dict],
    stop_fn: Callable[[str], None],
    repo_files: dict[str, str],
    test_source: str,
    touched_paths: list[str],
    vendor_wheels: dict[str, bytes] | None = None,
) -> dict:
    session_id = start_fn()
    try:
        conflict = _check_pyproject_mutmut_conflict(repo_files)
        if conflict:
            return {"error": conflict}

        test_dir = _find_test_directory(repo_files)
        test_path = f"{test_dir}/test_generated.py" if test_dir else "test_generated.py"

        file_content = [{"path": path, "text": text} for path, text in repo_files.items()]
        file_content.append({"path": test_path, "text": test_source})
        invoke_fn(session_id, "writeFiles", {"content": file_content})

        if vendor_wheels:
            # The sandbox's default network mode has no route to PyPI (see
            # docs/spike-code-interpreter.md) -- pytest/mutmut and their full
            # dependency closure are bundled into the image and uploaded here
            # as binary blobs, then installed offline via --find-links.
            wheel_content = [{"path": f"vendor_wheels/{name}", "blob": data} for name, data in vendor_wheels.items()]
            invoke_fn(session_id, "writeFiles", {"content": wheel_content})
            invoke_fn(
                session_id,
                "executeCommand",
                {"command": "pip install --quiet --no-index --find-links=vendor_wheels pytest mutmut"},
            )
        else:
            invoke_fn(session_id, "executeCommand", {"command": "pip install --quiet pytest mutmut"})

        pytest_result = invoke_fn(session_id, "executeCommand", {"command": f"python -m pytest {test_path} -v"})
        tests_passed = pytest_result.get("exitCode", 1) == 0

        if not tests_passed:
            return {
                "tests_passed": False,
                "score": 0.0,
                "killed": 0,
                "survived": 0,
                "surviving_mutants": [],
                "pytest_output": pytest_result.get("stdout", ""),
            }

        # setup.cfg construction must stay in sync with mutation.py's
        # run_mutation() -- see that function for why these are the correct
        # mutmut 3.x config keys (source_paths / pytest_add_cli_args_test_selection,
        # not the mutmut 2.x paths_to_mutate/tests_dir/runner keys) and why
        # paths are newline-joined (configparser + mutmut's list-valued options
        # split on "\n", not ",").
        paths_arg = "\n    ".join(touched_paths)

        # mutmut only copies source_paths (the touched/mutated files) plus a
        # fixed whitelist (tests/, lockfiles, setup.cfg) into its isolated
        # "mutants/" staging dir for the test run -- untouched dependency
        # modules (e.g. app/repository.py, imported by conftest.py) never
        # get copied there, so the copied conftest.py's imports 404 even
        # though the same files work fine outside mutmut's sandbox. also_copy
        # closes that gap by copying every top-level source package too.
        top_level_dirs = sorted({p.split("/", 1)[0] for p in repo_files if "/" in p and not p.startswith("tests/")})
        also_copy_arg = "\n    ".join(top_level_dirs)

        setup_cfg = (
            "[mutmut]\n"
            f"source_paths={paths_arg}\n"
            f"pytest_add_cli_args_test_selection={test_path}\n"
            f"also_copy={also_copy_arg}\n"
        )
        invoke_fn(session_id, "writeFiles", {"content": [{"path": "setup.cfg", "text": setup_cfg}]})

        # Run "mutmut run" and "mutmut results" as separate commands (not
        # chained with ";") so the run's own exit code can be checked --
        # mirrors mutation.py's two separate subprocess.run() calls. mutmut
        # 3.x's "run" subcommand only accepts --max-children, not
        # --no-progress (a stale pre-3.x flag), so it is invoked bare.
        run_result = invoke_fn(session_id, "executeCommand", {"command": "mutmut run"})
        if run_result.get("exitCode", 1) != 0:
            # mutmut's "run" command exits 0 on a normal completion
            # (regardless of how many mutants survived -- survivor counts are
            # not reflected in the exit code); it only exits non-zero when
            # the run itself failed (e.g. the clean/unmutated test run
            # failed, or a config/setup error), which means the "mutation_raw_output"
            # this would otherwise return is not trustworthy.
            return {"error": f"mutmut run failed: {run_result.get('stdout', '')}\n{run_result.get('stderr', '')}"}

        results_result = invoke_fn(session_id, "executeCommand", {"command": "mutmut results"})

        return {
            "tests_passed": True,
            "pytest_output": pytest_result.get("stdout", ""),
            "mutation_raw_output": run_result.get("stdout", "") + "\n" + results_result.get("stdout", ""),
        }
    finally:
        stop_fn(session_id)
