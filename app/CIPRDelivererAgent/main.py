import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import boto3
import requests
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from git_ops import clone_repo, create_branch_and_commit, push_branch
from workflow_injector import ensure_killjoy_workflow
from pr_builder import build_pr_body, open_pull_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_SECRET_ARN = os.environ["GITHUB_SECRET_ARN"]
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

app = BedrockAgentCoreApp()


def _get_github_token() -> str:
    resp = secrets_client.get_secret_value(SecretId=GITHUB_SECRET_ARN)
    data = json.loads(resp["SecretString"])
    return data["github_token"]


def _github_post(url: str, json_body: dict, headers: dict) -> dict:
    token = _get_github_token()
    full_headers = {**headers, "Authorization": f"Bearer {token}"}
    resp = requests.post(url, json=json_body, headers=full_headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


@app.entrypoint
async def handler(payload: dict) -> dict:
    run_result = payload.get("run_result", {})
    if not run_result.get("tests_passed") or "score" not in run_result:
        return {"error": "refusing to open a PR: sandbox run has not proven tests pass with a computed mutation score"}

    repo_url = payload["repo_url"]
    repo_full_name = payload["repo_full_name"]
    pr_number = payload["pr_number"]
    base_ref = payload["base_ref"]
    base_branch = payload["base_branch"]
    test_source = payload["test_source"]

    owner, repo = repo_full_name.split("/")
    branch_name = f"killjoy/pr-{pr_number}-{uuid.uuid4().hex[:8]}"

    token = _get_github_token()
    work_dir = Path(tempfile.mkdtemp(prefix="killjoy-"))
    try:
        clone_repo(repo_url, token, work_dir, base_ref)

        files = {f"tests/killjoy/generated/test_pr_{pr_number}.py": test_source}
        create_branch_and_commit(work_dir, branch_name, files, f"test: add Killjoy integration tests for PR #{pr_number}")

        workflow_changed = ensure_killjoy_workflow(work_dir)
        if workflow_changed:
            from git_ops import _run_git
            _run_git(["add", ".github/workflows/killjoy-integration.yml"], work_dir)
            _run_git(["commit", "-m", "ci: add Killjoy integration test workflow"], work_dir)

        push_branch(work_dir, branch_name)

        pr_body = build_pr_body(run_result)
        pr = open_pull_request(
            _github_post,
            owner=owner,
            repo=repo,
            branch=branch_name,
            base=base_branch,
            title=f"Killjoy: integration tests for PR #{pr_number}",
            body=pr_body,
            labels=["ai-generated"],
        )

        return {"pr_url": pr["html_url"]}
    except Exception as exc:
        logger.error("CI/PR Deliverer failed: %s", exc)
        return {"error": str(exc)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    app.run()
