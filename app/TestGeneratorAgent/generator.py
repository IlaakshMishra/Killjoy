import ast
import re
from typing import Callable

SYSTEM_PROMPT = """You write pytest integration tests that exercise multiple
real internal components of a codebase together. You are given the PR diff,
an environment map describing each layer and which layers should be
substituted (only genuine outer edges — real internal code must run
unmodified), the existing conftest fixtures available to you, the actual
source of every layer/fixture file, and an EXHAUSTIVE list of every
method/function signature that actually exists in those files.

Rules:
- Use only the fixtures listed in the environment map; do not invent new ones.
- Never mock or stub a layer marked "none_real_execution" — call its real code.
- Only substitute layers explicitly marked "in_memory" or "mock".
- The "Exact available API" list is exhaustive: any method, function, or
  attribute not in that list does not exist in this codebase. Never call
  something not on the list, even if the name would be reasonable for it to
  have. If you need a capability that isn't listed, write the test using
  only what is listed instead of inventing the missing piece.
- Write complete, runnable pytest test functions — no placeholders, no TODOs.
- Return ONLY a python code block, no prose before or after.
"""


def _extract_signatures(source: str) -> list[str]:
    """AST-extracted, exhaustive method/function signatures for one file.

    Raw source alone wasn't a strong enough grounding signal -- the LLM
    hallucinated plausible-sounding methods (e.g. "add_order_item") even
    when the real source was right there in the prompt. An explicit,
    unambiguous signature list is a much harder constraint to ignore.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        return ", ".join(a.arg for a in node.args.args)

    signatures = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signatures.append(f"def {node.name}({_params(node)})")
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    signatures.append(f"{node.name}.{member.name}({_params(member)})")
    return signatures


def _bare_method_names(signatures: list[str]) -> set[str]:
    """Strip "Class.method(args)" / "def func(args)" signatures down to just
    the callable name, for cheap membership checks against generated calls."""
    names = set()
    for sig in signatures:
        head = sig.split("(", 1)[0]
        name = head.split(".")[-1]
        if name.startswith("def "):
            name = name[len("def "):]
        names.add(name)
    return names


def _find_unknown_fixture_calls(test_source: str, fixture_names: set[str], known_methods: set[str]) -> list[str]:
    """Find "<fixture>.<method>(...)" calls in the generated test where
    <fixture> is a known pytest fixture parameter but <method> doesn't
    appear anywhere in the exhaustive signature list -- i.e. a likely
    hallucinated API. Heuristic (no real type inference), but it directly
    targets the observed failure mode: calling a plausible-sounding method
    that doesn't actually exist on the real object.
    """
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return []

    unknown = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_fixtures = {a.arg for a in func.args.args if a.arg in fixture_names}
        if not local_fixtures:
            continue
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in local_fixtures
                and node.func.attr not in known_methods
            ):
                unknown.append(f"{node.func.value.id}.{node.func.attr}")
    return sorted(set(unknown))


def _build_prompt(
    diff: str,
    env_map: dict,
    surviving_mutants: list[dict] | None,
    whole_repo_files: dict[str, str] | None = None,
) -> str:
    whole_repo_files = whole_repo_files or {}
    layers_desc = "\n".join(
        f"- {l['name']} ({l['path']}): role={l['role']}, substitute={l['substitute']}"
        for l in env_map.get("layers", [])
    )
    fixtures_desc = "\n".join(
        f"- {f['name']} (defined in {f['file']})" for f in env_map.get("fixtures", [])
    )

    relevant_paths = list(dict.fromkeys(
        [l["path"] for l in env_map.get("layers", [])] + [f["file"] for f in env_map.get("fixtures", [])]
    ))
    sources_desc = "\n\n".join(
        f"### {path}\n```python\n{whole_repo_files[path]}\n```"
        for path in relevant_paths
        if path in whole_repo_files
    )

    api_lines = []
    for path in relevant_paths:
        if path in whole_repo_files:
            api_lines.extend(_extract_signatures(whole_repo_files[path]))
    api_desc = "\n".join(f"- {sig}" for sig in api_lines) or "- (none)"

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"PR diff:\n{diff}\n\n"
        f"Layers:\n{layers_desc}\n\n"
        f"Available fixtures:\n{fixtures_desc}\n\n"
        f"Exact available API (exhaustive — nothing else exists):\n{api_desc}\n\n"
        f"Relevant source files (for context/behavior, not new API surface):\n{sources_desc}\n"
    )

    if surviving_mutants:
        mutant_lines = "\n".join(
            f"- {m['file']}:{m['line']} — {m['description']}" for m in surviving_mutants
        )
        prompt += (
            "\n\nThe previous round of tests did NOT catch these mutations "
            "(the mutated code ran and no test failed). Write additional or "
            "revised tests that would fail against each of these mutations:\n"
            f"{mutant_lines}\n"
        )

    return prompt


def generate_tests(
    diff: str,
    env_map: dict,
    llm_invoke: Callable[[str], str],
    surviving_mutants: list[dict] | None = None,
    whole_repo_files: dict[str, str] | None = None,
    max_attempts: int = 3,
) -> str:
    whole_repo_files = whole_repo_files or {}
    relevant_paths = list(dict.fromkeys(
        [l["path"] for l in env_map.get("layers", [])] + [f["file"] for f in env_map.get("fixtures", [])]
    ))
    known_methods = set()
    for path in relevant_paths:
        if path in whole_repo_files:
            known_methods |= _bare_method_names(_extract_signatures(whole_repo_files[path]))
    fixture_names = {f["name"] for f in env_map.get("fixtures", [])}

    prompt = _build_prompt(diff, env_map, surviving_mutants, whole_repo_files)
    source = None

    for attempt in range(max_attempts):
        raw = llm_invoke(prompt)
        code_block_match = re.search(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
        source = code_block_match.group(1) if code_block_match else raw
        validate_test_source(source)

        unknown_calls = _find_unknown_fixture_calls(source, fixture_names, known_methods) if known_methods else []
        if not unknown_calls:
            return source

        if attempt < max_attempts - 1:
            prompt += (
                "\n\nYour previous attempt called methods that do not exist "
                f"in this codebase: {', '.join(unknown_calls)}. Rewrite the "
                "tests using ONLY methods from the exact available API list "
                "above -- do not repeat these calls."
            )

    return source


def validate_test_source(source: str) -> None:
    compile(source, "<generated_test>", "exec")
