from pathlib import Path

WORKFLOW_CONTENT = """name: Killjoy Integration Tests

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  killjoy-integration-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Install dependencies
        run: pip install pytest
      - name: Run Killjoy-generated integration tests
        run: python -m pytest tests/killjoy/generated/ -v
"""


def ensure_killjoy_workflow(repo_path: Path) -> bool:
    workflow_dir = repo_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_dir / "killjoy-integration.yml"

    if workflow_path.exists() and workflow_path.read_text() == WORKFLOW_CONTENT:
        return False

    workflow_path.write_text(WORKFLOW_CONTENT)
    return True
