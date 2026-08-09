import logging
import os
import re
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

from sandbox import execute_in_sandbox
from mutation import run_mutation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
VENDOR_WHEELS_DIR = Path(__file__).parent / "vendor_wheels"

app = BedrockAgentCoreApp()


def _load_vendor_wheels() -> dict[str, bytes]:
    if not VENDOR_WHEELS_DIR.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in VENDOR_WHEELS_DIR.glob("*.whl")}


def _parse_mutation_output(raw_output: str, touched_paths: list[str]) -> dict:
    # mutmut 3.x prints one mutant per line as "<qualified.mutant_id>: survived"
    # (e.g. "    target.x_add_bonus__mutmut_1: survived"), not the older
    # "<numeric_id>. ... survived" format the brief originally assumed. This
    # must stay in sync with mutation.py's run_mutation(), which parses the
    # same real mutmut 3.7.0 output (verified in Task 13) with this same
    # layered fallback -- see mutation.py for the reasoning behind each tier.
    survived_ids = re.findall(r"^\s*(\S+):\s*survived", raw_output, re.MULTILINE | re.IGNORECASE)
    if not survived_ids:
        survived_ids = re.findall(r"^(\d+)\.\s.*survived", raw_output, re.MULTILINE | re.IGNORECASE)
    if not survived_ids:
        survived_ids = [
            line.split(":")[0].strip()
            for line in raw_output.splitlines()
            if "survived" in line.lower()
        ]

    killed_match = re.search(r"(\d+)/(\d+)", raw_output)
    total = int(killed_match.group(2)) if killed_match else len(survived_ids)
    survived = len(survived_ids)
    killed = max(total - survived, 0)
    if total == 0:
        # A zero-total result means mutation testing never actually ran
        # (empty/unparseable mutmut output) -- NOT that the code is perfect.
        # Silently returning score=1.0 here would let a broken mutation run
        # look like a flawless one.
        return {"error": "mutation testing produced no parseable mutant results (total_mutants=0)"}
    score = killed / total
    surviving_mutants = [
        {"id": mid, "file": touched_paths[0] if touched_paths else "", "line": 0, "description": f"mutant {mid} survived"}
        for mid in survived_ids
    ]
    return {"score": round(score, 4), "killed": killed, "survived": survived, "surviving_mutants": surviving_mutants}


def _consume_stream(invoke_result: dict) -> dict:
    """Drain the EventStream returned by CodeInterpreter.invoke() into a single
    dict-like result with stdout/stderr/exitCode, mirroring
    scripts/spike_code_interpreter.py's consume_stream()/print_command_result()
    pattern from the Task 7 spike (docs/spike-code-interpreter.md).

    invoke() does NOT return an inline dict of results -- it returns
    {"ResponseMetadata": ..., "sessionId": ..., "stream": <botocore.eventstream.EventStream>}.
    The stream is a lazy, one-shot iterator; each event looks like:
        {"result": {"content": [...], "structuredContent": {"stdout": ..., "stderr": ...,
                                                              "exitCode": ..., "executionTime": ...},
                     "isError": bool}}
    writeFiles-style calls only carry "content" (text), no "structuredContent".
    sandbox.py's execute_in_sandbox() expects invoke_fn to return a dict it can
    call .get("exitCode", ...) / .get("stdout", ...) on, so this must fully
    drain the stream and flatten it before returning.
    """
    stdout_parts = []
    stderr_parts = []
    exit_code = 0
    for event in invoke_result.get("stream", []):
        result = event.get("result", {})
        structured = result.get("structuredContent")
        if structured:
            if structured.get("stdout"):
                stdout_parts.append(structured["stdout"])
            if structured.get("stderr"):
                stderr_parts.append(structured["stderr"])
            exit_code = structured.get("exitCode", exit_code)
        else:
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            if texts:
                stdout_parts.append(" ".join(texts))
            if result.get("isError"):
                exit_code = exit_code or 1
    return {"stdout": "".join(stdout_parts), "stderr": "".join(stderr_parts), "exitCode": exit_code}


@app.entrypoint
async def handler(payload: dict) -> dict:
    repo_files = payload.get("repo_files")
    test_source = payload.get("test_source")
    touched_paths = payload.get("touched_paths", [])

    if not repo_files or not test_source:
        return {"error": "repo_files and test_source are required"}

    interpreter = CodeInterpreter(region=AWS_REGION)

    def start_fn():
        interpreter.start()
        return "session"

    def invoke_fn(session_id, tool_name, params):
        raw_result = interpreter.invoke(tool_name, params)
        return _consume_stream(raw_result)

    def stop_fn(session_id):
        interpreter.stop()

    result = execute_in_sandbox(
        start_fn, invoke_fn, stop_fn, repo_files, test_source, touched_paths, vendor_wheels=_load_vendor_wheels()
    )

    if "error" in result:
        return {"error": result["error"]}

    if not result["tests_passed"]:
        return {"error": "generated tests failed against real code", "pytest_output": result.get("pytest_output", "")}

    mutation_summary = _parse_mutation_output(result.get("mutation_raw_output", ""), touched_paths)

    if "error" in mutation_summary:
        return {"error": mutation_summary["error"]}

    return {"tests_passed": True, **mutation_summary}


if __name__ == "__main__":
    app.run()
