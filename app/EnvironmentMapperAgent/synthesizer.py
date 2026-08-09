import json
import re
from typing import Callable

SYSTEM_PROMPT = """You map a codebase's intra-application integration surface.
Given a structural scan (modules, functions, imports, fixtures, top-level
directories), classify each module into a layer (e.g. api, service,
repository) and decide, for each layer, whether integration tests should run
its real code unmodified ("none_real_execution") or substitute it with an
in-memory/mocked version because it is an outer edge (database driver, HTTP
client, third-party SDK) ("in_memory" or "mock"). Prefer running real internal
code; only recommend a substitute for genuine outer edges.

Return ONLY valid JSON, no prose. Exact structure:
{
  "layers": [
    {"name": "<layer>", "path": "<module path>", "role": "boundary|internal", "substitute": "none_real_execution|in_memory|mock"}
  ],
  "fixtures": [{"name": "<fixture name>", "file": "<file path>"}],
  "outer_edges": [{"boundary": "<what it is>", "substitute_recommendation": "<in_memory|mock>"}]
}
"""


def synthesize_env_map(raw_scan: dict, llm_invoke: Callable[[str], str]) -> dict:
    prompt = (
        f"{SYSTEM_PROMPT}\n\nStructural scan:\n{json.dumps(raw_scan, indent=2)}"
    )
    raw = llm_invoke(prompt)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"Environment Mapper LLM response contained no JSON: {raw!r}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"Environment Mapper LLM response contained malformed JSON: {match.group(0)!r}") from e
