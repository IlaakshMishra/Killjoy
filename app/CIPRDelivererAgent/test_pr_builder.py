from pr_builder import build_pr_body, open_pull_request


def test_build_pr_body_states_pass_fail_score_and_survivors():
    run_result = {
        "tests_passed": True,
        "score": 0.85,
        "killed": 17,
        "survived": 3,
        "surviving_mutants": [
            {"id": "42", "file": "app/service.py", "line": 12, "description": "changed >= to > and no test failed"}
        ],
    }

    body = build_pr_body(run_result)

    assert "tests passing against real code" in body.lower() or "passed" in body.lower()
    assert "0.85" in body or "85" in body
    assert "app/service.py:12" in body
    assert "changed >= to > and no test failed" in body


def test_open_pull_request_sends_expected_payload_and_labels():
    captured = {}
    labels_captured = {}

    def fake_github_post(url, json_body, headers):
        # Only capture the PR creation call, not the labels call
        if "pulls" in url:
            captured["url"] = url
            captured["json_body"] = json_body
            captured["headers"] = headers
            return {"html_url": "https://github.com/acme/widgets/pull/99", "number": 99}

        # The labels call (POST .../issues/{number}/labels)
        labels_captured["url"] = url
        labels_captured["json_body"] = json_body
        labels_captured["headers"] = headers
        return {}

    result = open_pull_request(
        fake_github_post,
        owner="acme",
        repo="widgets",
        branch="killjoy/pr-5-abc123",
        base="main",
        title="Killjoy: integration tests for PR #5",
        body="body text",
        labels=["ai-generated"],
    )

    assert result["number"] == 99
    assert captured["json_body"]["head"] == "killjoy/pr-5-abc123"
    assert captured["json_body"]["base"] == "main"
    assert captured["json_body"]["base"] != captured["json_body"]["head"]

    assert "labels" in labels_captured["url"]
    assert labels_captured["json_body"] == {"labels": ["ai-generated"]}


def test_open_pull_request_surfaces_pr_url_when_labeling_fails():
    """A partial failure (PR opens successfully but applying the
    'ai-generated' label guardrail fails) must not be swallowed into a
    generic error -- the PR is already live on the real repo at that point,
    so the raised error must include its URL so it's actionable."""

    def fake_github_post(url, json_body, headers):
        if "pulls" in url:
            return {"html_url": "https://github.com/acme/widgets/pull/99", "number": 99}
        raise RuntimeError("GitHub API returned 403")

    try:
        open_pull_request(
            fake_github_post,
            owner="acme",
            repo="widgets",
            branch="killjoy/pr-5-abc123",
            base="main",
            title="Killjoy: integration tests for PR #5",
            body="body text",
            labels=["ai-generated"],
        )
        assert False, "expected open_pull_request to raise"
    except RuntimeError as exc:
        assert "https://github.com/acme/widgets/pull/99" in str(exc)
        assert "GitHub API returned 403" in str(exc)
