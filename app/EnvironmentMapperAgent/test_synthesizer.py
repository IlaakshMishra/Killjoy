import json
import pytest
from synthesizer import synthesize_env_map


def test_synthesize_env_map_parses_llm_json_response():
    raw_scan = {
        "modules": [
            {"path": "app/api.py", "functions": ["handle_get_order"], "imports": ["app.service"]},
            {"path": "app/service.py", "functions": ["OrderService.calculate_total"], "imports": ["app.repository"]},
            {"path": "app/repository.py", "functions": ["InMemoryOrderRepository.get_page"], "imports": []},
        ],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "directories": ["app", "tests"],
    }

    fake_response = json.dumps({
        "layers": [
            {"name": "api", "path": "app/api.py", "role": "boundary", "substitute": "none_real_execution"},
            {"name": "service", "path": "app/service.py", "role": "internal", "substitute": "none_real_execution"},
            {"name": "repository", "path": "app/repository.py", "role": "internal", "substitute": "in_memory"},
        ],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "outer_edges": [],
    })

    def fake_llm_invoke(prompt: str) -> str:
        assert "app/api.py" in prompt
        return fake_response

    env_map = synthesize_env_map(raw_scan, fake_llm_invoke)

    assert len(env_map["layers"]) == 3
    repository_layer = next(l for l in env_map["layers"] if l["name"] == "repository")
    assert repository_layer["substitute"] == "in_memory"


def test_synthesize_env_map_recovers_json_embedded_in_prose():
    raw_scan = {"modules": [], "fixtures": [], "directories": []}

    def fake_llm_invoke(prompt: str) -> str:
        return 'Here is the map:\n{"layers": [], "fixtures": [], "outer_edges": []}\nDone.'

    env_map = synthesize_env_map(raw_scan, fake_llm_invoke)
    assert env_map == {"layers": [], "fixtures": [], "outer_edges": []}


def test_synthesize_env_map_raises_value_error_for_malformed_json_in_prose():
    raw_scan = {"modules": [], "fixtures": [], "directories": []}

    def fake_llm_invoke(prompt: str) -> str:
        return 'prose {not valid json} more prose'

    with pytest.raises(ValueError) as exc_info:
        synthesize_env_map(raw_scan, fake_llm_invoke)

    assert "malformed JSON" in str(exc_info.value)
