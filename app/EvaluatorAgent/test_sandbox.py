from sandbox import execute_in_sandbox, _find_test_directory


def test_execute_in_sandbox_runs_pytest_then_mutation_via_injected_calls():
    calls = []

    def fake_start():
        calls.append("start")
        return "session-1"

    def fake_invoke(session_id, tool_name, params):
        calls.append((tool_name, params.get("command", params.get("content"))))
        if tool_name == "writeFiles":
            return {"ok": True}
        if tool_name == "executeCommand" and "pytest" in params["command"] and "mutmut" not in params["command"]:
            return {"stdout": "1 passed", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut run":
            return {"stdout": "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut results":
            return {"stdout": "", "exitCode": 0}
        return {"stdout": "", "exitCode": 0}

    def fake_stop(session_id):
        calls.append("stop")

    result = execute_in_sandbox(
        start_fn=fake_start,
        invoke_fn=fake_invoke,
        stop_fn=fake_stop,
        repo_files={"app/target.py": "def f():\n    return 1\n"},
        test_source="def test_f():\n    from app.target import f\n    assert f() == 1\n",
        touched_paths=["app/target.py"],
    )

    assert calls[0] == "start"
    assert calls[-1] == "stop"
    assert result["tests_passed"] is True


def test_execute_in_sandbox_mutation_raw_output_contains_total_from_run_and_survivors_from_results():
    def fake_start():
        return "session-1"

    def fake_invoke(session_id, tool_name, params):
        if tool_name == "writeFiles":
            return {"ok": True}
        if tool_name == "executeCommand" and "pytest" in params["command"] and "mutmut" not in params["command"]:
            return {"stdout": "1 passed", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut run":
            return {"stdout": "1/2  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 1", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut results":
            return {"stdout": "    app.target.x_add_bonus__mutmut_1: survived\n", "exitCode": 0}
        return {"stdout": "", "exitCode": 0}

    def fake_stop(session_id):
        pass

    result = execute_in_sandbox(
        start_fn=fake_start,
        invoke_fn=fake_invoke,
        stop_fn=fake_stop,
        repo_files={"app/target.py": "def f():\n    return 1\n"},
        test_source="def test_f():\n    from app.target import f\n    assert f() == 1\n",
        touched_paths=["app/target.py"],
    )

    assert "1/2" in result["mutation_raw_output"]
    assert "survived" in result["mutation_raw_output"]


def test_execute_in_sandbox_uploads_vendor_wheels_and_installs_offline():
    calls = []

    def fake_start():
        return "session-1"

    def fake_invoke(session_id, tool_name, params):
        calls.append((tool_name, params.get("command", params.get("content"))))
        if tool_name == "writeFiles":
            return {"ok": True}
        if tool_name == "executeCommand" and "pytest" in params["command"] and "mutmut" not in params["command"]:
            return {"stdout": "1 passed", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut run":
            return {"stdout": "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut results":
            return {"stdout": "", "exitCode": 0}
        return {"stdout": "", "exitCode": 0}

    def fake_stop(session_id):
        pass

    result = execute_in_sandbox(
        start_fn=fake_start,
        invoke_fn=fake_invoke,
        stop_fn=fake_stop,
        repo_files={"app/target.py": "def f():\n    return 1\n"},
        test_source="def test_f():\n    from app.target import f\n    assert f() == 1\n",
        touched_paths=["app/target.py"],
        vendor_wheels={"pytest-9.1.1-py3-none-any.whl": b"fake-wheel-bytes"},
    )

    wheel_upload_calls = [c for c in calls if c[0] == "writeFiles" and isinstance(c[1], list) and c[1] and "blob" in c[1][0]]
    assert len(wheel_upload_calls) == 1
    assert wheel_upload_calls[0][1][0]["path"] == "vendor_wheels/pytest-9.1.1-py3-none-any.whl"
    assert wheel_upload_calls[0][1][0]["blob"] == b"fake-wheel-bytes"

    install_calls = [c for c in calls if c[0] == "executeCommand" and "pip install" in c[1]]
    assert install_calls == [("executeCommand", "pip install --quiet --no-index --find-links=vendor_wheels pytest mutmut")]

    assert result["tests_passed"] is True


def test_execute_in_sandbox_falls_back_to_online_install_without_vendor_wheels():
    calls = []

    def fake_start():
        return "session-1"

    def fake_invoke(session_id, tool_name, params):
        calls.append((tool_name, params.get("command", params.get("content"))))
        if tool_name == "writeFiles":
            return {"ok": True}
        if tool_name == "executeCommand" and "pytest" in params["command"] and "mutmut" not in params["command"]:
            return {"stdout": "1 passed", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut run":
            return {"stdout": "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut results":
            return {"stdout": "", "exitCode": 0}
        return {"stdout": "", "exitCode": 0}

    def fake_stop(session_id):
        pass

    execute_in_sandbox(
        start_fn=fake_start,
        invoke_fn=fake_invoke,
        stop_fn=fake_stop,
        repo_files={"app/target.py": "def f():\n    return 1\n"},
        test_source="def test_f():\n    from app.target import f\n    assert f() == 1\n",
        touched_paths=["app/target.py"],
    )

    install_calls = [c for c in calls if c[0] == "executeCommand" and "pip install" in c[1]]
    assert install_calls == [("executeCommand", "pip install --quiet pytest mutmut")]


def test_find_test_directory_returns_deepest_conftest_dir():
    assert _find_test_directory({"tests/conftest.py": "", "app/service.py": ""}) == "tests"


def test_find_test_directory_returns_empty_string_when_no_conftest():
    assert _find_test_directory({"app/service.py": ""}) == ""


def test_find_test_directory_prefers_deepest_when_multiple_conftests_exist():
    assert _find_test_directory({
        "conftest.py": "",
        "tests/integration/conftest.py": "",
        "tests/conftest.py": "",
    }) == "tests/integration"


def test_execute_in_sandbox_writes_generated_test_alongside_conftest_not_at_root():
    calls = []

    def fake_start():
        return "session-1"

    def fake_invoke(session_id, tool_name, params):
        calls.append((tool_name, params.get("command", params.get("content"))))
        if tool_name == "writeFiles":
            return {"ok": True}
        if tool_name == "executeCommand" and "pytest" in params["command"] and "mutmut" not in params["command"]:
            return {"stdout": "1 passed", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut run":
            return {"stdout": "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut results":
            return {"stdout": "", "exitCode": 0}
        return {"stdout": "", "exitCode": 0}

    def fake_stop(session_id):
        pass

    execute_in_sandbox(
        start_fn=fake_start,
        invoke_fn=fake_invoke,
        stop_fn=fake_stop,
        repo_files={"app/service.py": "def f(): return 1\n", "tests/conftest.py": "import pytest\n"},
        test_source="def test_f(service):\n    assert True\n",
        touched_paths=["app/service.py"],
    )

    write_files_calls = [c for c in calls if c[0] == "writeFiles"]
    written_paths = [f["path"] for f in write_files_calls[0][1]]
    assert "tests/test_generated.py" in written_paths
    assert "test_generated.py" not in written_paths

    pytest_calls = [c for c in calls if c[0] == "executeCommand" and "pytest" in c[1] and "mutmut" not in c[1]]
    assert pytest_calls == [("executeCommand", "python -m pytest tests/test_generated.py -v")]


def test_execute_in_sandbox_setup_cfg_also_copies_untouched_source_dirs():
    calls = []

    def fake_start():
        return "session-1"

    def fake_invoke(session_id, tool_name, params):
        calls.append((tool_name, params.get("command", params.get("content"))))
        if tool_name == "writeFiles":
            return {"ok": True}
        if tool_name == "executeCommand" and "pytest" in params["command"] and "mutmut" not in params["command"]:
            return {"stdout": "1 passed", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut run":
            return {"stdout": "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0", "exitCode": 0}
        if tool_name == "executeCommand" and params["command"] == "mutmut results":
            return {"stdout": "", "exitCode": 0}
        return {"stdout": "", "exitCode": 0}

    def fake_stop(session_id):
        pass

    execute_in_sandbox(
        start_fn=fake_start,
        invoke_fn=fake_invoke,
        stop_fn=fake_stop,
        repo_files={
            "app/service.py": "",
            "app/repository.py": "",  # untouched dependency, not in touched_paths
            "tests/conftest.py": "",
        },
        test_source="def test_f(service):\n    assert True\n",
        touched_paths=["app/service.py"],
    )

    setup_cfg_calls = [c for c in calls if c[0] == "writeFiles" and isinstance(c[1], list) and c[1][0]["path"] == "setup.cfg"]
    assert len(setup_cfg_calls) == 1
    setup_cfg_text = setup_cfg_calls[0][1][0]["text"]
    assert "also_copy=app" in setup_cfg_text
    assert "tests" not in setup_cfg_text.split("also_copy=")[1]
