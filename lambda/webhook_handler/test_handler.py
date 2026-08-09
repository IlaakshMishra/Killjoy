import hashlib
import hmac
import json

from handler import verify_signature, lambda_handler, dispatch_handler


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_correct_signature():
    body = b'{"action": "opened"}'
    secret = "top-secret"
    assert verify_signature(body, _sign(body, secret), secret) is True


def test_verify_signature_rejects_wrong_signature():
    body = b'{"action": "opened"}'
    assert verify_signature(body, "sha256=deadbeef", "top-secret") is False


def test_verify_signature_rejects_missing_header():
    assert verify_signature(b"{}", "", "top-secret") is False


def test_lambda_handler_invokes_orchestrator_for_opened_pr():
    secret = "top-secret"
    body_dict = {
        "action": "opened",
        "repository": {"clone_url": "https://github.com/acme/widgets.git", "full_name": "acme/widgets"},
        "pull_request": {
            "number": 5,
            "title": "Add feature",
            "base": {"sha": "base123", "ref": "master"},
            "head": {"sha": "head456"},
        },
    }
    body_bytes = json.dumps(body_dict).encode("utf-8")
    event = {
        "headers": {
            "x-hub-signature-256": _sign(body_bytes, secret),
            "x-github-event": "pull_request",
        },
        "body": body_bytes.decode("utf-8"),
        "isBase64Encoded": False,
    }

    invoked_with = {}

    def fake_get_secret():
        return secret

    def fake_invoke_orchestrator(payload, session_id):
        invoked_with["payload"] = payload
        return {"status": "success"}

    class FakeContext:
        aws_request_id = "req-1"

    response = lambda_handler(event, FakeContext(), fake_get_secret, fake_invoke_orchestrator)

    assert response["statusCode"] == 200
    assert invoked_with["payload"]["pr_number"] == 5
    assert invoked_with["payload"]["repo_full_name"] == "acme/widgets"
    assert invoked_with["payload"]["base_branch"] == "master"


def test_lambda_handler_rejects_bad_signature():
    event = {
        "headers": {"x-hub-signature-256": "sha256=wrong", "x-github-event": "pull_request"},
        "body": "{}",
        "isBase64Encoded": False,
    }

    class FakeContext:
        aws_request_id = "req-2"

    response = lambda_handler(event, FakeContext(), lambda: "top-secret", lambda p, s: {})
    assert response["statusCode"] == 401


def test_lambda_handler_skips_non_pr_events():
    event = {
        "headers": {"x-github-event": "push"},
        "body": "{}",
        "isBase64Encoded": False,
    }

    class FakeContext:
        aws_request_id = "req-3"

    response = lambda_handler(event, FakeContext(), lambda: "unused", lambda p, s: {})
    assert response["statusCode"] == 200
    assert "skipped" in response["body"]


def test_lambda_handler_skips_non_opened_or_synchronize_actions():
    body_dict = {"action": "closed", "repository": {}, "pull_request": {}}
    body_bytes = json.dumps(body_dict).encode("utf-8")
    secret = "top-secret"
    event = {
        "headers": {"x-hub-signature-256": _sign(body_bytes, secret), "x-github-event": "pull_request"},
        "body": body_bytes.decode("utf-8"),
        "isBase64Encoded": False,
    }

    class FakeContext:
        aws_request_id = "req-4"

    response = lambda_handler(event, FakeContext(), lambda: secret, lambda p, s: {})
    assert response["statusCode"] == 200
    assert "skipped" in response["body"]


def test_dispatch_handler_dispatches_async_for_opened_pr():
    secret = "top-secret"
    body_dict = {"action": "opened", "repository": {}, "pull_request": {}}
    body_bytes = json.dumps(body_dict).encode("utf-8")
    event = {
        "headers": {
            "x-hub-signature-256": _sign(body_bytes, secret),
            "x-github-event": "pull_request",
        },
        "body": body_bytes.decode("utf-8"),
        "isBase64Encoded": False,
    }

    dispatched = {}

    def fake_dispatch_async(evt):
        dispatched["event"] = evt

    class FakeContext:
        aws_request_id = "req-5"

    response = dispatch_handler(event, FakeContext(), lambda: secret, fake_dispatch_async)

    assert response["statusCode"] == 202
    assert dispatched["event"] == event


def test_dispatch_handler_rejects_bad_signature_without_dispatching():
    event = {
        "headers": {"x-hub-signature-256": "sha256=wrong", "x-github-event": "pull_request"},
        "body": "{}",
        "isBase64Encoded": False,
    }

    dispatched = {}

    class FakeContext:
        aws_request_id = "req-6"

    response = dispatch_handler(event, FakeContext(), lambda: "top-secret", lambda evt: dispatched.setdefault("called", True))

    assert response["statusCode"] == 401
    assert "called" not in dispatched


def test_dispatch_handler_skips_non_pr_events_without_dispatching():
    event = {
        "headers": {"x-github-event": "push"},
        "body": "{}",
        "isBase64Encoded": False,
    }

    dispatched = {}

    class FakeContext:
        aws_request_id = "req-7"

    response = dispatch_handler(event, FakeContext(), lambda: "unused", lambda evt: dispatched.setdefault("called", True))

    assert response["statusCode"] == 200
    assert "skipped" in response["body"]
    assert "called" not in dispatched
