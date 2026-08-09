import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("evaluator_agent_main", Path(__file__).parent / "main.py")
_evaluator_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_evaluator_main)
_consume_stream = _evaluator_main._consume_stream
_parse_mutation_output = _evaluator_main._parse_mutation_output
_load_vendor_wheels = _evaluator_main._load_vendor_wheels


def test_parse_mutation_output_handles_real_mutmut_3x_survived_format():
    # Real mutmut 3.7.0 `mutmut results` output format (verified in Task 13's
    # mutation.py work): "<qualified.mutant_id>: survived" per line, not the
    # brief's originally assumed "<numeric_id>. ... survived" format. A run
    # summary line elsewhere in the raw output (mimicking mutmut's own
    # "N/M" progress line) supplies the total mutant count.
    raw_output = (
        "2/4  🙁 2  ⏰ 0  🤔 0  🙁 2  🔇 0\n"
        "\n"
        "To apply a mutant on disk:\n"
        "    mutmut apply <id>\n"
        "\n"
        "Survived 🙁 (2)\n"
        "---- target.x_add_bonus__mutmut_1 (1)\n"
        "\n"
        "target.x_add_bonus__mutmut_1: survived\n"
        "target.x_add_bonus__mutmut_3: survived\n"
    )

    result = _parse_mutation_output(raw_output, touched_paths=["app/target.py"])

    assert result["survived"] == 2
    assert result["killed"] == 2
    assert result["score"] == 0.5
    assert result["score"] != 1.0
    ids = {m["id"] for m in result["surviving_mutants"]}
    assert ids == {"target.x_add_bonus__mutmut_1", "target.x_add_bonus__mutmut_3"}


def test_parse_mutation_output_does_not_hardcode_perfect_score_when_unparseable():
    # A zero-total result means mutation testing never actually ran (empty
    # or unparseable mutmut output) -- NOT that the code is perfect. This
    # must surface as a hard error, not a silently perfect score=1.0.
    result = _parse_mutation_output("", touched_paths=["app/target.py"])
    assert "error" in result
    assert "score" not in result


def test_parse_mutation_output_all_killed_scores_full_marks():
    raw_output = "4/4  🎉 4  ⏰ 0  🤔 0  🙁 0  🔇 0\n"
    result = _parse_mutation_output(raw_output, touched_paths=["app/target.py"])
    assert result["survived"] == 0
    assert result["killed"] == 4
    assert result["score"] == 1.0


def test_consume_stream_flattens_executecommand_style_events():
    events = [
        {
            "result": {
                "content": [{"type": "text", "text": "ignored"}],
                "structuredContent": {
                    "stdout": "1 passed\n",
                    "stderr": "",
                    "exitCode": 0,
                    "executionTime": 0.1,
                },
                "isError": False,
            }
        }
    ]
    invoke_result = {"stream": iter(events)}

    result = _consume_stream(invoke_result)

    assert result == {"stdout": "1 passed\n", "stderr": "", "exitCode": 0}


def test_consume_stream_flattens_writefiles_style_events():
    events = [
        {
            "result": {
                "content": [{"type": "text", "text": "Successfully wrote all 1 files"}],
                "isError": False,
            }
        }
    ]
    invoke_result = {"stream": iter(events)}

    result = _consume_stream(invoke_result)

    assert result["stdout"] == "Successfully wrote all 1 files"
    assert result["exitCode"] == 0


def test_consume_stream_surfaces_error_exit_code_when_iserror_true_without_structured_content():
    events = [
        {
            "result": {
                "content": [{"type": "text", "text": "boom"}],
                "isError": True,
            }
        }
    ]
    invoke_result = {"stream": iter(events)}

    result = _consume_stream(invoke_result)

    assert result["stdout"] == "boom"
    assert result["exitCode"] == 1


def test_consume_stream_combines_multiple_events_in_order():
    events = [
        {
            "result": {
                "structuredContent": {"stdout": "first\n", "stderr": "", "exitCode": 0},
                "isError": False,
            }
        },
        {
            "result": {
                "structuredContent": {"stdout": "second\n", "stderr": "warn\n", "exitCode": 1},
                "isError": False,
            }
        },
    ]
    invoke_result = {"stream": iter(events)}

    result = _consume_stream(invoke_result)

    assert result["stdout"] == "first\nsecond\n"
    assert result["stderr"] == "warn\n"
    assert result["exitCode"] == 1


def test_load_vendor_wheels_reads_whl_files_as_bytes(tmp_path, monkeypatch):
    (tmp_path / "pytest-9.1.1-py3-none-any.whl").write_bytes(b"fake-pytest-wheel")
    (tmp_path / "mutmut-3.7.0-py3-none-any.whl").write_bytes(b"fake-mutmut-wheel")
    (tmp_path / "not-a-wheel.txt").write_text("ignored")

    monkeypatch.setattr(_evaluator_main, "VENDOR_WHEELS_DIR", tmp_path)

    result = _load_vendor_wheels()

    assert result == {
        "pytest-9.1.1-py3-none-any.whl": b"fake-pytest-wheel",
        "mutmut-3.7.0-py3-none-any.whl": b"fake-mutmut-wheel",
    }


def test_load_vendor_wheels_returns_empty_dict_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_evaluator_main, "VENDOR_WHEELS_DIR", tmp_path / "does-not-exist")

    assert _load_vendor_wheels() == {}
