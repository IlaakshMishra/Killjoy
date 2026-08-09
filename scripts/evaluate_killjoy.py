"""
Runs Killjoy's own pipeline against the demo-repo's planted-bug scenarios,
entirely in-process (no AWS), by wiring run_pipeline's injected callables to
local calls into each agent's core module. Reports the two numbers the ADR's
"Testing Killjoy Itself" section requires:

1. Does the mutation score improve (or hold, never regress) round over round?
2. Does Killjoy ever generate a test that fails against the FIXED/correct
   version of the code (a false positive — worse than no test at all)?

Usage: python scripts/evaluate_killjoy.py
Requires: OPENAI/ANTHROPIC credentials are NOT needed for the LLM-backed
stages if KILLJOY_FAKE_LLM=1 is set, which substitutes a deterministic stub
LLM for local/offline runs. For a real evaluation, unset it and configure AWS
Bedrock credentials so the real Test Generator/Environment Mapper prompts run.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app" / "EnvironmentMapperAgent"))
sys.path.insert(0, str(ROOT / "app" / "TestGeneratorAgent"))
sys.path.insert(0, str(ROOT / "app" / "EvaluatorAgent"))
sys.path.insert(0, str(ROOT / "app" / "OrchestratorAgent"))

from scanner import scan_repo
from synthesizer import synthesize_env_map
from generator import generate_tests
from runner import run_pytest
from mutation import run_mutation
from pipeline import run_pipeline

DEMO_REPO = ROOT / "demo-repo"
KNOWN_BUGS_PATH = ROOT / "eval" / "known_bugs.json"


def _fake_llm_invoke(prompt: str) -> str:
    """Deterministic stub used when KILLJOY_FAKE_LLM=1 — for offline dry-runs
    of the pipeline wiring, not a substitute for real evaluation quality."""
    if "layers" in prompt.lower() and "environment map" not in prompt.lower():
        return json.dumps({"layers": [], "fixtures": [], "outer_edges": []})
    return "def test_placeholder():\n    assert True\n"


def _swap_in_scenario(scenario: dict) -> str:
    """Copies the scenario's buggy file over demo-repo's real file. Returns
    the original (fixed) content so the caller can restore it afterward."""
    target_path = DEMO_REPO / scenario["target_path"]
    original_content = target_path.read_text()
    scenario_file = ROOT / scenario["scenario_file"]
    target_path.write_text(scenario_file.read_text())
    return original_content


def _restore_target(scenario: dict, original_content: str) -> None:
    (DEMO_REPO / scenario["target_path"]).write_text(original_content)


def _make_llm_invoke():
    if os.environ.get("KILLJOY_FAKE_LLM") == "1":
        return _fake_llm_invoke

    from langchain_aws import ChatBedrockConverse
    llm = ChatBedrockConverse(model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"))

    def _invoke(prompt: str) -> str:
        response = llm.invoke([{"role": "user", "content": prompt}])
        return response.content if hasattr(response, "content") else str(response)

    return _invoke


def _local_env_mapper(payload: dict, llm_invoke) -> dict:
    raw_scan = scan_repo(Path(payload["repo_path"]))
    return synthesize_env_map(raw_scan, llm_invoke)


def _local_test_generator(payload: dict, llm_invoke) -> dict:
    try:
        source = generate_tests(payload["diff"], payload["env_map"], llm_invoke, payload.get("surviving_mutants"))
    except SyntaxError as exc:
        return {"error": str(exc)}
    return {"test_source": source}


def _local_evaluator(payload: dict) -> dict:
    repo_path = DEMO_REPO
    test_file = "tests/test_generated_eval.py"
    (repo_path / test_file).write_text(payload["test_source"])
    try:
        pytest_result = run_pytest(repo_path, test_file)
        if not pytest_result["passed"]:
            return {"error": "generated tests failed against real code", "pytest_output": pytest_result["summary"]}

        mutation_result = run_mutation(repo_path, payload["touched_paths"], test_file)
        return {"tests_passed": True, **mutation_result}
    finally:
        (repo_path / test_file).unlink(missing_ok=True)


def _local_ci_deliverer(payload: dict) -> dict:
    return {"pr_url": "local-eval-no-pr-opened"}


def _check_no_false_positive(test_source: str, original_content: str, scenario: dict) -> bool:
    """Restores the fixed/correct file, runs the generated test against it,
    and returns True if it PASSES (i.e. no false positive). Assumes the
    buggy variant is currently swapped in; restores it again afterward so
    the caller's own bookkeeping of "currently buggy" stays accurate."""
    target_path = DEMO_REPO / scenario["target_path"]
    buggy_content = target_path.read_text()
    target_path.write_text(original_content)

    test_file = "tests/test_generated_eval_fixed_check.py"
    (DEMO_REPO / test_file).write_text(test_source)
    try:
        result = run_pytest(DEMO_REPO, test_file)
        return result["passed"]
    finally:
        (DEMO_REPO / test_file).unlink(missing_ok=True)
        target_path.write_text(buggy_content)


def main():
    scenarios = json.loads(KNOWN_BUGS_PATH.read_text())
    llm_invoke = _make_llm_invoke()

    false_positive_count = 0
    no_coverage_rounds = 0
    all_scenario_rounds = {}

    for scenario in scenarios:
        print(f"\n=== Scenario: {scenario['id']} ===")
        original_content = _swap_in_scenario(scenario)

        try:
            request = {
                "repo_path": str(DEMO_REPO),
                "diff": f"diff --git a/{scenario['target_path']} b/{scenario['target_path']}\n+ integration test target",
                "repo_url": "local",
                "repo_full_name": "local/demo-repo",
                "pr_number": 0,
                "base_ref": "HEAD",
                "repo_files": {scenario["target_path"]: (DEMO_REPO / scenario["target_path"]).read_text()},
                "touched_paths": [scenario["target_path"]],
            }

            result = run_pipeline(
                request,
                invoke_env_mapper=lambda p: _local_env_mapper(p, llm_invoke),
                invoke_test_generator=lambda p: _local_test_generator(p, llm_invoke),
                invoke_evaluator=_local_evaluator,
                invoke_ci_deliverer=_local_ci_deliverer,
            )

            scores = [r["score"] for r in result["rounds"]]
            all_scenario_rounds[scenario["id"]] = scores
            print(f"status={result['status']}, mutation scores by round={scores}")

            for i, r in enumerate(result["rounds"]):
                if r.get("killed", 0) + r.get("survived", 0) == 0:
                    no_coverage_rounds += 1
                    print(f"  round {i}: NO COVERAGE (score={r['score']} is not meaningful — mutmut found zero mutants for this test)")

            final_test_source = result.get("final_test_source")
            if final_test_source:
                passes_on_fixed_code = _check_no_false_positive(final_test_source, original_content, scenario)
                if not passes_on_fixed_code:
                    false_positive_count += 1
                    print(f"FALSE POSITIVE: generated test for {scenario['id']} fails against fixed code")
        finally:
            _restore_target(scenario, original_content)

    print("\n=== Summary ===")
    for scenario_id, scores in all_scenario_rounds.items():
        improving = all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1))
        print(f"{scenario_id}: rounds={scores}, monotonically non-decreasing={improving}")

    print(f"\nFalse positives (generated tests failing on correct code): {false_positive_count}")
    print(f"Rounds with no mutation coverage (score not meaningful): {no_coverage_rounds}")


if __name__ == "__main__":
    main()
