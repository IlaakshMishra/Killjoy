import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from guardrails import reserve_run
from pipeline import run_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
ENV_MAPPER_ARN = os.environ["ENV_MAPPER_ARN"]
TEST_GENERATOR_ARN = os.environ["TEST_GENERATOR_ARN"]
EVALUATOR_ARN = os.environ["EVALUATOR_ARN"]
CI_PR_DELIVERER_ARN = os.environ["CI_PR_DELIVERER_ARN"]
GITHUB_SECRET_ARN = os.environ["GITHUB_SECRET_ARN"]
PR_RUNS_TABLE = os.environ.get("PR_RUNS_TABLE", "killjoy-pr-runs")
DAILY_COUNTER_TABLE = os.environ.get("DAILY_COUNTER_TABLE", "killjoy-daily-counter")
DAILY_PR_CEILING = int(os.environ.get("DAILY_PR_CEILING", "5"))

dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
bedrock_agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

app = BedrockAgentCoreApp()


def _ensure_session_id(session_id: str) -> str:
    if len(session_id) < 33:
        session_id = session_id + uuid.uuid4().hex
    return session_id[:128]


def _invoke_agent(agent_arn: str, payload: dict, label: str) -> dict:
    session_id = _ensure_session_id(f"killjoy-{label}-{uuid.uuid4().hex}")
    resp = bedrock_agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        payload=json.dumps(payload).encode("utf-8"),
        runtimeSessionId=session_id,
    )
    raw_bytes = resp["response"].read()
    text = raw_bytes.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"error": f"non-json response from {label}: {text[:500]}"}


def _get_github_token() -> str:
    resp = secrets_client.get_secret_value(SecretId=GITHUB_SECRET_ARN)
    return json.loads(resp["SecretString"])["github_token"]


def _clone_for_analysis(repo_url: str, token: str, ref: str) -> Path:
    import subprocess
    dest = Path(tempfile.mkdtemp(prefix="killjoy-orch-"))
    authenticated_url = repo_url.replace("https://", f"https://x-access-token:{token}@")
    try:
        subprocess.run(["git", "clone", authenticated_url, str(dest)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "checkout", ref], cwd=str(dest), check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # Whichever command failed (clone or checkout), dest must not leak on disk.
        shutil.rmtree(dest, ignore_errors=True)
        # Sanitize token from error message to prevent leaks in logs. The checkout
        # command's args never contain the token-bearing URL, so this replace is a
        # harmless no-op when it's a checkout failure.
        sanitized_message = str(e).replace(f"https://x-access-token:{token}@", "https://x-access-token:***@")
        if e.stderr:
            sanitized_message += f"\nStderr: {e.stderr.replace(f'https://x-access-token:{token}@', 'https://x-access-token:***@')}"
        raise RuntimeError(sanitized_message) from None
    return dest


def _build_diff(repo_path: Path, base_ref: str, head_ref: str) -> str:
    import subprocess
    result = subprocess.run(
        ["git", "diff", f"{base_ref}...{head_ref}"], cwd=str(repo_path),
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _touched_paths(diff: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)


def _read_repo_files(repo_path: Path, touched_paths: list[str]) -> dict:
    files = {}
    for rel_path in touched_paths:
        file_path = repo_path / rel_path
        if file_path.exists():
            files[rel_path] = file_path.read_text()
    return files


def _read_whole_repo_files(repo_path: Path) -> dict:
    """Read every .py file in the cloned repo (excluding .git/__pycache__,
    matching scanner.py's own exclusion pattern) as a relative-path -> content
    dict, so the Environment Mapper (running in a separate container with its
    own filesystem) can reconstruct the whole repo's structure by value rather
    than being handed a filesystem path that only exists in this container."""
    files = {}
    for py_file in sorted(repo_path.rglob("*.py")):
        rel_path = py_file.relative_to(repo_path)
        if any(part in (".git", "__pycache__") for part in rel_path.parts):
            continue
        files[str(rel_path)] = py_file.read_text()
    return files


@app.entrypoint
async def handler(payload: dict) -> dict:
    repo_full_name = payload["repo_full_name"]
    pr_number = payload["pr_number"]
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pr_key = f"{repo_full_name}#{pr_number}"

    allowed, reason = reserve_run(dynamodb_client, pr_key, date_key, DAILY_PR_CEILING, PR_RUNS_TABLE, DAILY_COUNTER_TABLE)
    if not allowed:
        logger.info("Run rejected for %s: %s", pr_key, reason)
        return {"status": "skipped", "reason": reason}

    token = _get_github_token()
    repo_path = None
    try:
        try:
            repo_path = _clone_for_analysis(payload["repo_url"], token, payload["head_ref"])
            diff = _build_diff(repo_path, payload["base_ref"], payload["head_ref"])
            touched_paths = _touched_paths(diff)
            repo_files = _read_repo_files(repo_path, touched_paths)
            whole_repo_files = _read_whole_repo_files(repo_path)
        except Exception as exc:
            logger.error("Pipeline failed at stage %s: %s", "clone_or_diff", exc)
            return {
                "status": "failed",
                "stage_failed": "clone_or_diff",
                "reason": str(exc),
                "pr_url": None,
                "rounds": [],
                "final_test_source": None,
            }

        request = {
            "repo_path": str(repo_path),
            "diff": diff,
            "repo_url": payload["repo_url"],
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "base_ref": payload["base_ref"],
            "base_branch": payload["base_branch"],
            "repo_files": repo_files,
            "whole_repo_files": whole_repo_files,
            "touched_paths": touched_paths,
        }

        result = run_pipeline(
            request,
            invoke_env_mapper=lambda p: _invoke_agent(ENV_MAPPER_ARN, p, "env-mapper"),
            invoke_test_generator=lambda p: _invoke_agent(TEST_GENERATOR_ARN, p, "test-generator"),
            invoke_evaluator=lambda p: _invoke_agent(EVALUATOR_ARN, p, "evaluator"),
            invoke_ci_deliverer=lambda p: _invoke_agent(CI_PR_DELIVERER_ARN, p, "ci-pr-deliverer"),
        )

        if result["status"] == "failed":
            logger.error("Pipeline failed at stage %s: %s", result["stage_failed"], result["reason"])

        return result
    finally:
        if repo_path:
            shutil.rmtree(repo_path, ignore_errors=True)


if __name__ == "__main__":
    app.run()
