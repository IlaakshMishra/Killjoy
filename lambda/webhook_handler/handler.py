import base64
import hashlib
import hmac
import json
import os
from typing import Callable

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")


def verify_signature(body: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


def lambda_handler(
    event: dict,
    context,
    get_webhook_secret: Callable[[], str],
    invoke_orchestrator: Callable[[dict, str], dict],
) -> dict:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body_raw = event.get("body", "")
    body_bytes = base64.b64decode(body_raw) if event.get("isBase64Encoded") else body_raw.encode("utf-8")

    if headers.get("x-github-event") != "pull_request":
        return {"statusCode": 200, "body": json.dumps({"skipped": "not a pull_request event"})}

    secret = get_webhook_secret()
    if not verify_signature(body_bytes, headers.get("x-hub-signature-256", ""), secret):
        return {"statusCode": 401, "body": json.dumps({"error": "invalid signature"})}

    payload = json.loads(body_bytes)
    action = payload.get("action")
    if action not in ("opened", "synchronize"):
        return {"statusCode": 200, "body": json.dumps({"skipped": f"action={action}"})}

    pr = payload["pull_request"]
    repo = payload["repository"]

    orchestrator_payload = {
        "repo_url": repo["clone_url"],
        "repo_full_name": repo["full_name"],
        "pr_number": pr["number"],
        "base_ref": pr["base"]["sha"],
        "base_branch": pr["base"]["ref"],
        "head_ref": pr["head"]["sha"],
        "pr_title": pr["title"],
    }

    session_id_seed = f"killjoy-{repo['full_name'].replace('/', '-')}-{pr['number']}-{context.aws_request_id}"
    session_id = (session_id_seed + "0" * 33)[:128] if len(session_id_seed) < 33 else session_id_seed[:128]

    result = invoke_orchestrator(orchestrator_payload, session_id)

    return {"statusCode": 200, "body": json.dumps({"orchestrator_result": result})}


def dispatch_handler(
    event: dict,
    context,
    get_webhook_secret: Callable[[], str],
    dispatch_async: Callable[[dict], None],
) -> dict:
    """API-Gateway-facing entrypoint.

    API Gateway HTTP APIs hard-cap the Lambda proxy integration at ~29s,
    but the Killjoy pipeline (env map -> generate -> mutate, up to 3
    rounds) routinely runs longer. So this verifies the signature and
    action synchronously (fast), then hands off to an async self-invoke
    and returns immediately, never blocking on invoke_agent_runtime.
    """
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body_raw = event.get("body", "")
    body_bytes = base64.b64decode(body_raw) if event.get("isBase64Encoded") else body_raw.encode("utf-8")

    if headers.get("x-github-event") != "pull_request":
        return {"statusCode": 200, "body": json.dumps({"skipped": "not a pull_request event"})}

    secret = get_webhook_secret()
    if not verify_signature(body_bytes, headers.get("x-hub-signature-256", ""), secret):
        return {"statusCode": 401, "body": json.dumps({"error": "invalid signature"})}

    payload = json.loads(body_bytes)
    if payload.get("action") not in ("opened", "synchronize"):
        return {"statusCode": 200, "body": json.dumps({"skipped": f"action={payload.get('action')}"})}

    dispatch_async(event)
    return {"statusCode": 202, "body": json.dumps({"dispatched": True})}


def _get_webhook_secret() -> str:
    secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)
    resp = secrets_client.get_secret_value(SecretId=os.environ["WEBHOOK_SECRET_ARN"])
    return resp["SecretString"]


def _invoke_orchestrator(payload: dict, session_id: str) -> dict:
    agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    resp = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["ORCHESTRATOR_ARN"],
        payload=json.dumps(payload).encode("utf-8"),
        runtimeSessionId=session_id,
    )
    return json.loads(resp["response"].read().decode("utf-8"))


def _dispatch_async(event: dict) -> None:
    lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    lambda_client.invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps({"_killjoy_worker": True, "gateway_event": event}).encode("utf-8"),
    )


def handler(event, context):
    if event.get("_killjoy_worker"):
        return lambda_handler(event["gateway_event"], context, _get_webhook_secret, _invoke_orchestrator)
    return dispatch_handler(event, context, _get_webhook_secret, _dispatch_async)
