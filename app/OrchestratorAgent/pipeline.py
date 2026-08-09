import logging
from typing import Callable

logger = logging.getLogger(__name__)


def run_pipeline(
    request: dict,
    invoke_env_mapper: Callable[[dict], dict],
    invoke_test_generator: Callable[[dict], dict],
    invoke_evaluator: Callable[[dict], dict],
    invoke_ci_deliverer: Callable[[dict], dict],
    max_mutation_rounds: int = 3,
) -> dict:
    rounds: list[dict] = []
    test_source = None

    def _failed(stage: str, reason: str) -> dict:
        return {
            "status": "failed",
            "stage_failed": stage,
            "reason": reason,
            "pr_url": None,
            "rounds": rounds,
            "final_test_source": test_source,
        }

    try:
        env_map = invoke_env_mapper({"repo_files": request["whole_repo_files"]})
    except Exception as exc:
        logger.error("environment_mapper stage failed: %s", exc)
        return _failed("environment_mapper", str(exc))

    if isinstance(env_map, dict) and "error" in env_map:
        return _failed("environment_mapper", env_map["error"])

    surviving_mutants = None
    last_evaluator_result = None

    for _round_number in range(max_mutation_rounds):
        try:
            generator_result = invoke_test_generator({
                "diff": request["diff"],
                "env_map": env_map,
                "surviving_mutants": surviving_mutants,
                "whole_repo_files": request["whole_repo_files"],
            })
        except Exception as exc:
            logger.error("test_generator stage failed: %s", exc)
            return _failed("test_generator", str(exc))

        if "error" in generator_result:
            return _failed("test_generator", generator_result["error"])

        test_source = generator_result["test_source"]

        try:
            evaluator_result = invoke_evaluator({
                # Full repo, not just touched files -- the generated test
                # commonly imports untouched dependency modules (repository,
                # conftest fixtures, __init__.py) that must exist in the
                # sandbox for collection to succeed at all.
                "repo_files": request["whole_repo_files"],
                "test_source": test_source,
                "touched_paths": request["touched_paths"],
            })
        except Exception as exc:
            logger.error("evaluator stage failed: %s", exc)
            return _failed("evaluator", str(exc))

        if "error" in evaluator_result:
            return _failed("evaluator", evaluator_result["error"])

        rounds.append(evaluator_result)
        last_evaluator_result = evaluator_result

        surviving_mutants = evaluator_result.get("surviving_mutants", [])
        if not surviving_mutants:
            break

    try:
        ci_result = invoke_ci_deliverer({
            "repo_url": request["repo_url"],
            "repo_full_name": request["repo_full_name"],
            "pr_number": request["pr_number"],
            "base_ref": request["base_ref"],
            "base_branch": request["base_branch"],
            "test_source": test_source,
            "run_result": last_evaluator_result,
        })
    except Exception as exc:
        logger.error("ci_pr_deliverer stage failed: %s", exc)
        return _failed("ci_pr_deliverer", str(exc))

    if "error" in ci_result:
        return _failed("ci_pr_deliverer", ci_result["error"])

    return {
        "status": "success",
        "stage_failed": None,
        "reason": None,
        "pr_url": ci_result["pr_url"],
        "rounds": rounds,
        "final_test_source": test_source,
    }
