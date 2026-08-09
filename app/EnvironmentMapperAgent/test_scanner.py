from pathlib import Path
from scanner import scan_repo


def test_scan_repo_finds_modules_functions_and_fixtures(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    (app_dir / "service.py").write_text(
        "from app.repository import Repo\n\n"
        "class OrderService:\n"
        "    def calculate_total(self, order_id):\n"
        "        return 0\n"
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def repository():\n"
        "    return None\n"
    )

    result = scan_repo(tmp_path)

    module_paths = [m["path"] for m in result["modules"]]
    assert "app/service.py" in module_paths

    service_module = next(m for m in result["modules"] if m["path"] == "app/service.py")
    assert "OrderService.calculate_total" in service_module["functions"]
    assert "app.repository" in service_module["imports"]

    fixture_names = [f["name"] for f in result["fixtures"]]
    assert "repository" in fixture_names

    assert "app" in result["directories"]
    assert "tests" in result["directories"]
