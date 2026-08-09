from pathlib import Path
from workflow_injector import ensure_killjoy_workflow, WORKFLOW_CONTENT


def test_ensure_killjoy_workflow_creates_file_when_missing(tmp_path):
    changed = ensure_killjoy_workflow(tmp_path)

    workflow_path = tmp_path / ".github" / "workflows" / "killjoy-integration.yml"
    assert changed is True
    assert workflow_path.read_text() == WORKFLOW_CONTENT


def test_ensure_killjoy_workflow_is_idempotent(tmp_path):
    ensure_killjoy_workflow(tmp_path)
    changed_second_time = ensure_killjoy_workflow(tmp_path)

    assert changed_second_time is False
