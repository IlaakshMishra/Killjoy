from pipeline import run_pipeline


def _make_request():
    return {
        "repo_path": "/tmp/fake-repo",
        "diff": "diff --git a/app/service.py b/app/service.py",
        "repo_url": "https://github.com/acme/widgets.git",
        "repo_full_name": "acme/widgets",
        "pr_number": 5,
        "base_ref": "abc123",
        "base_branch": "master",
        "repo_files": {"app/service.py": "def f(): return 1"},
        "whole_repo_files": {"app/service.py": "def f(): return 1", "app/__init__.py": ""},
        "touched_paths": ["app/service.py"],
    }


def test_pipeline_succeeds_when_first_round_kills_all_mutants():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}
    calls = {"test_generator": 0, "evaluator": 0}

    def fake_env_mapper(payload):
        assert payload == {"repo_files": _make_request()["whole_repo_files"]}
        return env_map

    def fake_test_generator(payload):
        calls["test_generator"] += 1
        assert payload["env_map"] == env_map
        return {"test_source": "def test_x(): assert True"}

    def fake_evaluator(payload):
        calls["evaluator"] += 1
        return {"tests_passed": True, "score": 1.0, "killed": 5, "survived": 0, "surviving_mutants": []}

    def fake_ci_deliverer(payload):
        assert payload["run_result"]["tests_passed"] is True
        return {"pr_url": "https://github.com/acme/widgets/pull/99"}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer)

    assert result["status"] == "success"
    assert result["pr_url"] == "https://github.com/acme/widgets/pull/99"
    assert calls["test_generator"] == 1
    assert calls["evaluator"] == 1
    assert len(result["rounds"]) == 1


def test_pipeline_retries_test_generator_when_mutants_survive_then_succeeds():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}
    round_scores = iter([0.5, 1.0])
    survivors_by_round = iter([[{"id": "1", "file": "app/service.py", "line": 3, "description": "survived"}], []])
    generator_calls = []

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        generator_calls.append(payload.get("surviving_mutants"))
        return {"test_source": "def test_x(): assert True"}

    def fake_evaluator(payload):
        score = next(round_scores)
        survivors = next(survivors_by_round)
        return {"tests_passed": True, "score": score, "killed": 1, "survived": len(survivors), "surviving_mutants": survivors}

    def fake_ci_deliverer(payload):
        return {"pr_url": "https://github.com/acme/widgets/pull/100"}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer)

    assert result["status"] == "success"
    assert len(result["rounds"]) == 2
    assert generator_calls[0] is None
    assert generator_calls[1][0]["id"] == "1"
    assert result["rounds"][0]["score"] == 0.5
    assert result["rounds"][1]["score"] == 1.0


def test_pipeline_stops_after_max_rounds_and_still_opens_pr_with_best_effort_score():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        return {"test_source": "def test_x(): assert True"}

    def fake_evaluator(payload):
        return {"tests_passed": True, "score": 0.6, "killed": 3, "survived": 2, "surviving_mutants": [{"id": "1", "file": "a.py", "line": 1, "description": "x"}]}

    def fake_ci_deliverer(payload):
        return {"pr_url": "https://github.com/acme/widgets/pull/101"}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer, max_mutation_rounds=3)

    assert result["status"] == "success"
    assert len(result["rounds"]) == 3
    assert result["rounds"][-1]["score"] == 0.6


def test_pipeline_aborts_and_opens_no_pr_when_environment_mapper_fails():
    def fake_env_mapper(payload):
        raise RuntimeError("cannot parse repo")

    ci_deliverer_called = []

    def fake_ci_deliverer(payload):
        ci_deliverer_called.append(True)
        return {"pr_url": "should-not-happen"}

    result = run_pipeline(_make_request(), fake_env_mapper, lambda p: {}, lambda p: {}, fake_ci_deliverer)

    assert result["status"] == "failed"
    assert result["stage_failed"] == "environment_mapper"
    assert "cannot parse repo" in result["reason"]
    assert ci_deliverer_called == []


def test_pipeline_aborts_when_evaluator_reports_tests_failed_against_real_code():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        return {"test_source": "def test_x(): assert False"}

    def fake_evaluator(payload):
        return {"error": "generated tests failed against real code"}

    ci_deliverer_called = []

    def fake_ci_deliverer(payload):
        ci_deliverer_called.append(True)
        return {}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer)

    assert result["status"] == "failed"
    assert result["stage_failed"] == "evaluator"
    assert ci_deliverer_called == []
