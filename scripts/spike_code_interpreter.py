"""
Spike: confirm AgentCore Code Interpreter can run pytest against uploaded files.
Requires AWS credentials configured for an account with Bedrock AgentCore access.
Run manually: python scripts/spike_code_interpreter.py

NOTE ON invoke()'s RETURN VALUE:
`interpreter.invoke(...)` does NOT return an inline dict of results. It returns
`{"ResponseMetadata": ..., "sessionId": ..., "stream": <botocore.eventstream.EventStream>}`.
The `stream` is a lazy, one-shot iterator that must be consumed to see the actual
command output (stdout/stderr/exit code) -- printing the raw return value just shows
an unhelpful `<botocore.eventstream.EventStream object at 0x...>` repr. This mirrors
how the SDK's own `download_file`/`download_files` helpers parse the same shape
internally. See docs/spike-code-interpreter.md for the full writeup of this finding.
"""
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

REGION = "us-west-2"

TEST_FILE_CONTENT = """
def test_trivial():
    assert 1 + 1 == 2
"""


def consume_stream(invoke_result):
    """Drain the EventStream returned by invoke() into a list of result dicts.

    Each event looks like:
        {"result": {"content": [{"type": "text", "text": "..."}],
                     "structuredContent": {"stdout": "...", "stderr": "...",
                                            "exitCode": 0, "executionTime": 0.07},
                     "isError": false}}
    """
    events = []
    for event in invoke_result.get("stream", []):
        events.append(event)
    return events


def print_command_result(label, events):
    """Print the readable stdout/stderr/exitCode for an executeCommand/writeFiles call."""
    if not events:
        print(f"{label}: <no events in stream>")
        return
    for event in events:
        result = event.get("result", {})
        is_error = result.get("isError", False)
        structured = result.get("structuredContent")
        if structured:
            print(
                f"{label}: exitCode={structured.get('exitCode')} "
                f"executionTime={structured.get('executionTime')}s isError={is_error}"
            )
            if structured.get("stdout"):
                print(f"{label} stdout:\n{structured['stdout']}")
            if structured.get("stderr"):
                print(f"{label} stderr:\n{structured['stderr']}")
        else:
            # writeFiles and similar calls only have `content`, no structuredContent.
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            print(f"{label}: isError={is_error} -> {' '.join(texts)}")


def main():
    interpreter = CodeInterpreter(region=REGION)
    interpreter.start()
    try:
        write_result = interpreter.invoke(
            "writeFiles",
            {"content": [{"path": "test_trivial.py", "text": TEST_FILE_CONTENT}]},
        )
        print_command_result("writeFiles", consume_stream(write_result))

        install_result = interpreter.invoke(
            "executeCommand",
            {"command": "pip install --quiet pytest"},
        )
        print_command_result("pip install", consume_stream(install_result))

        run_result = interpreter.invoke(
            "executeCommand",
            {"command": "pytest test_trivial.py -v"},
        )
        print_command_result("pytest run", consume_stream(run_result))
    finally:
        interpreter.stop()


if __name__ == "__main__":
    main()
