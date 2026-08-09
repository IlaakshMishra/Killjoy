from pathlib import Path
from mutation import run_mutation


def test_run_mutation_reports_survivor_for_weak_test(tmp_path):
    (tmp_path / "target.py").write_text(
        "def add_bonus(total, item_count):\n"
        "    if item_count >= 5:\n"
        "        total = total * 1.1\n"
        "    return total\n"
    )
    # Weak test: only checks the no-bonus path, never exercises the boundary,
    # so the >= -> > mutation survives.
    (tmp_path / "test_target.py").write_text(
        "from target import add_bonus\n\n"
        "def test_no_bonus_below_threshold():\n"
        "    assert add_bonus(100, 1) == 100\n"
    )

    result = run_mutation(tmp_path, touched_paths=["target.py"], test_file_rel_path="test_target.py")

    assert result["survived"] >= 1
    assert result["score"] < 1.0
    assert any(m["file"] == "target.py" for m in result["surviving_mutants"])


def test_run_mutation_reports_full_score_for_strong_test(tmp_path):
    (tmp_path / "target.py").write_text(
        "def add_bonus(total, item_count):\n"
        "    if item_count >= 5:\n"
        "        total = total * 1.1\n"
        "    return total\n"
    )
    (tmp_path / "test_target.py").write_text(
        "from target import add_bonus\n\n"
        "def test_no_bonus_below_threshold():\n"
        "    assert add_bonus(100, 4) == 100\n\n"
        "def test_bonus_at_threshold():\n"
        "    assert round(add_bonus(100, 5), 2) == 110.0\n"
    )

    result = run_mutation(tmp_path, touched_paths=["target.py"], test_file_rel_path="test_target.py")

    assert result["survived"] == 0
    assert result["score"] == 1.0
