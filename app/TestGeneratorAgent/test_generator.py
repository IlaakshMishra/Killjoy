import pytest
from generator import generate_tests, validate_test_source, _extract_signatures, _find_unknown_fixture_calls


def test_generate_tests_returns_validated_python_source():
    env_map = {
        "layers": [
            {"name": "service", "path": "app/service.py", "role": "internal", "substitute": "none_real_execution"},
            {"name": "repository", "path": "app/repository.py", "role": "internal", "substitute": "in_memory"},
        ],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "outer_edges": [],
    }
    diff = "diff --git a/app/service.py b/app/service.py\n+ changed calculate_total"

    fake_source = (
        "def test_bulk_discount_boundary(service, repository):\n"
        "    repository.add_order(order_id=1, item_ids=['widget'] * 5)\n"
        "    assert service.calculate_total(order_id=1) == 45.0\n"
    )

    def fake_llm_invoke(prompt: str) -> str:
        assert "app/service.py" in prompt
        assert diff in prompt
        return f"```python\n{fake_source}```"

    result = generate_tests(diff, env_map, fake_llm_invoke)

    assert result.strip() == fake_source.strip()
    validate_test_source(result)  # must not raise


def test_generate_tests_includes_surviving_mutants_in_prompt():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}
    surviving_mutants = [
        {"id": "1", "file": "app/service.py", "line": 10, "description": "changed >= to > and no test failed"}
    ]

    def fake_llm_invoke(prompt: str) -> str:
        assert "changed >= to > and no test failed" in prompt
        return "def test_x():\n    assert True\n"

    generate_tests("diff", env_map, fake_llm_invoke, surviving_mutants=surviving_mutants)


def test_validate_test_source_raises_on_invalid_python():
    with pytest.raises(SyntaxError):
        validate_test_source("def test_broken(:\n    pass")


def test_generate_tests_includes_real_source_of_referenced_layers_and_fixtures():
    env_map = {
        "layers": [{"name": "repository", "path": "app/repository.py", "role": "boundary", "substitute": "in_memory"}],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "outer_edges": [],
    }
    whole_repo_files = {
        "app/repository.py": "class InMemoryOrderRepository:\n    def add_order(self, order_id, item_ids):\n        pass\n",
        "tests/conftest.py": "import pytest\n\n@pytest.fixture\ndef repository():\n    return InMemoryOrderRepository()\n",
        "app/unrelated.py": "def not_referenced_by_env_map(): pass\n",
    }

    def fake_llm_invoke(prompt: str) -> str:
        assert "add_order" in prompt
        assert "InMemoryOrderRepository" in prompt
        assert "not_referenced_by_env_map" not in prompt
        return "def test_x(): assert True"

    generate_tests("diff", env_map, fake_llm_invoke, whole_repo_files=whole_repo_files)


def test_generate_tests_warns_against_inventing_apis_not_in_source():
    def fake_llm_invoke(prompt: str) -> str:
        assert "does not exist in this codebase" in prompt
        return "def test_x(): assert True"

    generate_tests("diff", {"layers": [], "fixtures": [], "outer_edges": []}, fake_llm_invoke)


def test_extract_signatures_lists_methods_and_top_level_functions():
    source = (
        "def top_level(a, b):\n    pass\n\n"
        "class Foo:\n"
        "    def __init__(self, x):\n        pass\n"
        "    def bar(self, y):\n        pass\n"
    )

    signatures = _extract_signatures(source)

    assert "def top_level(a, b)" in signatures
    assert "Foo.__init__(self, x)" in signatures
    assert "Foo.bar(self, y)" in signatures


def test_extract_signatures_returns_empty_list_on_unparseable_source():
    assert _extract_signatures("def broken(:\n    pass") == []


def test_generate_tests_includes_exhaustive_api_list_not_just_raw_source():
    env_map = {
        "layers": [{"name": "repository", "path": "app/repository.py", "role": "boundary", "substitute": "in_memory"}],
        "fixtures": [],
        "outer_edges": [],
    }
    whole_repo_files = {
        "app/repository.py": "class InMemoryOrderRepository:\n    def add_order(self, order_id, item_ids):\n        pass\n",
    }

    def fake_llm_invoke(prompt: str) -> str:
        assert "Exact available API (exhaustive" in prompt
        assert "InMemoryOrderRepository.add_order(self, order_id, item_ids)" in prompt
        return "def test_x(): assert True"

    generate_tests("diff", env_map, fake_llm_invoke, whole_repo_files=whole_repo_files)


def test_find_unknown_fixture_calls_flags_calls_not_in_known_methods():
    test_source = (
        "def test_x(repository):\n"
        "    repository.add_order_item(1, 2, 3)\n"
        "    repository.add_order(1, [2])\n"
    )
    unknown = _find_unknown_fixture_calls(test_source, {"repository"}, {"add_order", "get_order"})
    assert unknown == ["repository.add_order_item"]


def test_find_unknown_fixture_calls_ignores_non_fixture_receivers():
    test_source = (
        "def test_x(repository):\n"
        "    helper = SomeHelper()\n"
        "    helper.whatever_method()\n"
    )
    unknown = _find_unknown_fixture_calls(test_source, {"repository"}, {"add_order"})
    assert unknown == []


def test_generate_tests_retries_when_hallucinated_call_detected():
    env_map = {
        "layers": [{"name": "repository", "path": "app/repository.py", "role": "boundary", "substitute": "in_memory"}],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "outer_edges": [],
    }
    whole_repo_files = {
        "app/repository.py": "class InMemoryOrderRepository:\n    def add_order(self, order_id, item_ids):\n        pass\n",
    }

    attempts = []
    bad_source = "def test_x(repository):\n    repository.add_order_item(1, 2)\n"
    good_source = "def test_x(repository):\n    repository.add_order(1, [2])\n"

    def fake_llm_invoke(prompt: str) -> str:
        attempts.append(prompt)
        if len(attempts) == 1:
            return f"```python\n{bad_source}```"
        assert "add_order_item" in prompt  # correction names the bad call
        return f"```python\n{good_source}```"

    result = generate_tests("diff", env_map, fake_llm_invoke, whole_repo_files=whole_repo_files)

    assert len(attempts) == 2
    assert result.strip() == good_source.strip()


def test_generate_tests_gives_up_after_max_attempts_and_returns_last_source():
    env_map = {
        "layers": [{"name": "repository", "path": "app/repository.py", "role": "boundary", "substitute": "in_memory"}],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "outer_edges": [],
    }
    whole_repo_files = {
        "app/repository.py": "class InMemoryOrderRepository:\n    def add_order(self, order_id, item_ids):\n        pass\n",
    }
    bad_source = "def test_x(repository):\n    repository.add_order_item(1, 2)\n"
    attempts = []

    def fake_llm_invoke(prompt: str) -> str:
        attempts.append(prompt)
        return f"```python\n{bad_source}```"

    result = generate_tests("diff", env_map, fake_llm_invoke, whole_repo_files=whole_repo_files, max_attempts=3)

    assert len(attempts) == 3
    assert result.strip() == bad_source.strip()
