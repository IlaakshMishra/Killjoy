# Spike: AWS Bedrock AgentCore Code Interpreter — can it run `pip install pytest && pytest`?

**Date:** 2026-08-08
**Account:** `<account-id>` (real AWS account, live spike, small real cost incurred)
**Region:** us-west-2 (no region configured via `aws configure get region`; used us-west-2 per the brief's default)
**Script:** `scripts/spike_code_interpreter.py` (matches the brief's Step 2 verbatim)

## Result: PARTIAL SUCCESS — SDK/tool names confirmed correct, but the end-to-end goal (`pip install pytest && pytest`) fails in the default sandbox

The ADR's single open risk was whether `bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter` with `.invoke("writeFiles", ...)` / `.invoke("executeCommand", ...)` can execute `pip install pytest && pytest` inside the AgentCore sandbox. The import path, class, method names, and `invoke()` signature from the brief are **all correct and worked exactly as written**. However, the live run surfaced a real, reproducible blocker: **the default system sandbox (`aws.codeinterpreter.v1`) has no route to the public internet / PyPI is unreachable from it**, so `pip install pytest` cannot reach PyPI, pytest never gets installed, and `pytest test_trivial.py -v` fails with "command not found". This is not a wrong-tool-name problem — it's a network-isolation property of the default sandbox that the ADR did not anticipate. (Note: this spike verified that PyPI/general public internet is unreachable — DNS resolution failure plus a raw-IP `curl` timeout — not that the sandbox has literally zero network capability of any kind. AWS's own docs describe the default "Sandbox" network mode as having *limited* external access, e.g. reachability to AWS services like S3; see "Real finding #2" below for exactly what was and wasn't tested.)

## SDK version

```
$ pip show bedrock-agentcore
Name: bedrock-agentcore
Version: 1.21.0
Summary: An SDK for using Bedrock AgentCore
Home-page:
Author:
Author-email: AWS <opensource@amazon.com>
License: Apache-2.0
Location: /Users/ilaakshmishra/.pyenv/versions/3.12.0/lib/python3.12/site-packages
Requires: boto3, botocore, pydantic, starlette, typing-extensions, urllib3, uvicorn, websockets
Required-by:
```

`boto3==1.43.67`, `botocore==1.43.67` were installed alongside it (also pulled in `uvicorn`, `starlette`, `s3transfer`, `jmespath`; pip flagged pre-existing version conflicts with `fastapi`/`fastmcp`/`mcp` in this environment's other installed packages — these did not affect the spike but are worth noting if this venv is reused for other work).

Import path and class matched the brief exactly — no deviation needed:
```python
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
```

`CodeInterpreter.__init__(self, region: str, session=None, integration_source=None)`, `.start(identifier='aws.codeinterpreter.v1', name=None, session_timeout_seconds=900) -> str`, `.invoke(method: str, params: Optional[Dict] = None)`, `.stop() -> bool` — all present, all match the brief's usage.

## Confirmed: the literal tool names in the brief work

- `interpreter.invoke("writeFiles", {"content": [{"path": ..., "text": ...}]})` — **works**, HTTP 200, returns `{"result": {"content": [{"type": "text", "text": "Successfully wrote all 1 files"}], "isError": false}}`.
- `interpreter.invoke("executeCommand", {"command": ...})` — **works** as a method name/shape (HTTP 200 in all cases); whether the *command itself* succeeds is a separate question (see below).

## Real finding #1: `invoke()`'s response is not directly printable — it must be consumed

Running `scripts/spike_code_interpreter.py` exactly as written in the brief produces this stdout (this is the literal, complete output of the script, unmodified):

```
writeFiles result: {'ResponseMetadata': {'RequestId': '6bed9506-a5b5-4722-beab-704f5a696bc2', 'HTTPStatusCode': 200, 'HTTPHeaders': {'date': 'Sat, 08 Aug 2026 15:39:41 GMT', 'content-type': 'application/vnd.amazon.eventstream', 'transfer-encoding': 'chunked', 'connection': 'keep-alive', 'x-amzn-requestid': '6bed9506-a5b5-4722-beab-704f5a696bc2', 'x-amzn-code-interpreter-session-id': '01KZH0FA9AZ935XP0YZK8H5SAZ'}, 'RetryAttempts': 0}, 'sessionId': '01KZH0FA9AZ935XP0YZK8H5SAZ', 'stream': <botocore.eventstream.EventStream object at 0x111f90d40>}
pip install result: {'ResponseMetadata': {'RequestId': '4a4732dd-15ea-461a-a2db-57e681a89e2f', 'HTTPStatusCode': 200, 'HTTPHeaders': {'date': 'Sat, 08 Aug 2026 15:39:50 GMT', 'content-type': 'application/vnd.amazon.eventstream', 'transfer-encoding': 'chunked', 'connection': 'keep-alive', 'x-amzn-requestid': '4a4732dd-15ea-461a-a2db-57e681a89e2f', 'x-amzn-code-interpreter-session-id': '01KZH0FA9AZ935XP0YZK8H5SAZ'}, 'RetryAttempts': 0}, 'sessionId': '01KZH0FA9AZ935XP0YZK8H5SAZ', 'stream': <botocore.eventstream.EventStream object at 0x111f91130>}
pytest result: {'ResponseMetadata': {'RequestId': 'd97626f0-9956-410e-94e9-c8cc01fa09c8', 'HTTPStatusCode': 200, 'HTTPHeaders': {'date': 'Sat, 08 Aug 2026 15:39:50 GMT', 'content-type': 'application/vnd.amazon.eventstream', 'transfer-encoding': 'chunked', 'connection': 'keep-alive', 'x-amzn-requestid': 'd97626f0-9956-410e-94e9-c8cc01fa09c8', 'x-amzn-code-interpreter-session-id': '01KZH0FA9AZ935XP0YZK8H5SAZ'}, 'RetryAttempts': 0}, 'sessionId': '01KZH0FA9AZ935XP0YZK8H5SAZ', 'stream': <botocore.eventstream.EventStream object at 0x111f91490>}
```

`interpreter.stop()` ran without error each time (returned `True`, no exception).

This is the real finding the brief's Step 3 was watching for: `invoke()` does **not** return a dict with inline stdout/exit-code fields. It returns `{'ResponseMetadata', 'sessionId', 'stream': <botocore.eventstream.EventStream>}`, and the `stream` is a lazy, one-shot iterator that must be consumed:

```python
for event in result["stream"]:
    # event looks like: {"result": {"content": [{"type": "text", "text": "..."}],
    #                                "structuredContent": {"stdout": "...", "stderr": "...",
    #                                                       "exitCode": 0, "executionTime": 0.07},
    #                                "isError": false}}
    ...
```

`content-type: application/vnd.amazon.eventstream` in the HTTP headers confirms this is a streamed response, consistent with how the SDK's own `download_file`/`download_files` helper methods parse it internally (they iterate `result["stream"]` looking for `event["result"]["content"]`). **Task 11's `sandbox.py` must consume the stream, not just log the top-level `invoke()` return value**, or it will never see real stdout/stderr/exit codes.

## Real finding #2: PyPI/the public internet is unreachable from the default sandbox — `pip install pytest` cannot succeed

After adding stream-consumption to see the actual per-command results (kept out of the committed script, since the brief's canonical script is preserved verbatim; this was done via an ad hoc diagnostic wrapper), the real per-command output was:

**`writeFiles`** — succeeded:
```json
{"result": {"content": [{"type": "text", "text": "Successfully wrote all 1 files"}], "isError": false}}
```

**`executeCommand` → `pip install --quiet pytest`** — failed, exit code 1:
```
WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='pypi.org', port=443): Failed to resolve 'pypi.org' ([Errno -2] Name or service not known)")': /simple/pytest/
WARNING: Retrying (Retry(total=3, ...)) ... Failed to resolve 'pypi.org' ...
WARNING: Retrying (Retry(total=2, ...)) ... Failed to resolve 'pypi.org' ...
WARNING: Retrying (Retry(total=1, ...)) ... Failed to resolve 'pypi.org' ...
WARNING: Retrying (Retry(total=0, ...)) ... Failed to resolve 'pypi.org' ...
ERROR: Could not find a version that satisfies the requirement pytest (from versions: none)
ERROR: No matching distribution found for pytest
```
`structuredContent`: `{"stdout": "", "stderr": "<above>", "exitCode": 1, "executionTime": 8.13}`

**`executeCommand` → `pytest test_trivial.py -v`** — failed, exit code 127:
```
/bin/sh: line 1: pytest: command not found
```
`structuredContent`: `{"stdout": "", "stderr": "/bin/sh: line 1: pytest: command not found\r\n", "exitCode": 127, "executionTime": 0.076}`

So **the stdout containing `1 passed` that the brief's Step 3 expected never occurred** — pytest was never installed, so it never ran the test file.

### This was reproduced twice, with identical results

A fresh session (new `start()`, new session ID) run a second time produced byte-for-byte the same DNS failure and the same `pytest: command not found`, ruling out a transient network blip.

### Root cause, characterized directly

Running diagnostics inside a third fresh session:

```
$ cat /etc/resolv.conf
nameserver 127.0.0.2
search .
```
(a stub/loopback resolver only — no real upstream DNS configured)

```
$ getent hosts pypi.org || echo NO_DNS
NO_DNS

$ python3 -c "import socket; print(socket.gethostbyname('pypi.org'))" || echo NO_PY_DNS
socket.gaierror: [Errno -2] Name or service not known
NO_PY_DNS

$ curl -sS -m 5 -o /dev/null -w '%{http_code}' https://pypi.org || echo CURL_FAILED
curl: (6) Could not resolve host: pypi.org
000CURL_FAILED

$ curl -sS -m 5 -o /dev/null -w '%{http_code}' http://93.184.216.34 || echo CURL_IP_FAILED
curl: (7) Failed to connect to 93.184.216.34 port 80 after 0ms: Could not connect to server
000CURL_IP_FAILED
```

DNS resolution fails **and** a raw IP connection also fails/times out — this confirms PyPI and general public internet hosts are unreachable, not just a broken DNS resolver. What this spike did **not** test: whether the sandbox can reach other AWS services (e.g. S3) — AWS's documentation describes the default "Sandbox" network mode as having *limited* external access (such as reachability to select AWS services), not literal zero network capability. So the precise, verified claim is: **no route to the public internet / PyPI is unreachable from `aws.codeinterpreter.v1`'s default network mode** — not "zero egress of any kind." Full public internet access (`networkConfiguration: {"networkMode": "PUBLIC"}`) is only exposed via `create_code_interpreter()` for a **custom** interpreter with an execution role, not via `start()`'s default identifier — untested here.

The sandbox does, however, ship a large pre-installed package set (`pip list` showed hundreds of packages — boto3, botocore, bokeh, pandas-adjacent scientific-stack packages, etc.) — but **pytest is not among them**:
```
$ pip list 2>&1 | grep -i pytest || echo PYTEST_NOT_IN_PIP_LIST
PYTEST_NOT_IN_PIP_LIST

$ which pytest || echo NO_PYTEST_BINARY
NO_PYTEST_BINARY

$ python3 -m pytest --version || echo PYTEST_MODULE_FAILED
No module named pytest
PYTEST_MODULE_FAILED
```

So there is no path to get pytest running in the default sandbox via `pip install` (PyPI unreachable) or by relying on a pre-installed copy (not present).

## Timing (wall clock, single run)

| Step | Time |
|---|---|
| `start()` | ~1.1–1.7s |
| `writeFiles` (incl. stream consumption) | ~0.14s |
| `pip install --quiet pytest` (incl. stream consumption, includes 5 internal retries before giving up) | ~8.1–8.4s |
| `pytest test_trivial.py -v` (incl. stream consumption) | ~0.07–0.24s |
| `stop()` | ~0.17–0.18s |
| **Total (start → writeFiles → install → run → stop)** | **~10–11s** |

(The brief's canonical script — which does not consume the stream and so does not wait on it — reported `0.25s user 0.06s system` / `10.857s total` via the shell's `time` builtin for the whole process; most of that wall time is server-side round-trip latency for `start()` and the (internally-retried) `pip install` call, not local CPU.)

## What Task 11's `sandbox.py` needs to change vs. the ADR's assumption

1. **Tool names are correct as-is**: `writeFiles` and `executeCommand` are real, working tool names on `invoke()`. No renaming needed there.
2. **Must consume `result["stream"]`** to get real stdout/stderr/exit code, following the same pattern as the SDK's own `download_file`: `for event in result["stream"]: sc = event["result"]["structuredContent"]` gives `{"stdout", "stderr", "exitCode", "executionTime"}`, and `event["result"]["isError"]` flags failure.
3. **The default system interpreter (`aws.codeinterpreter.v1`) cannot install packages from PyPI** — PyPI/the public internet is unreachable from its default network mode (verified: DNS resolution failure + raw-IP `curl` timeout; not verified: whether it can reach other AWS services like S3, which AWS's docs suggest may be reachable even in the default "Sandbox" network mode). Task 11 cannot rely on `pip install pytest && pytest` against the default identifier. Three possible remediations, **none of which were tested in this spike** (out of scope — would require provisioning an IAM execution role and/or a custom Code Interpreter resource, or an S3 bucket + upload, which is more real AWS infrastructure than this spike was authorized to stand up):
   - **Option A**: Create a **custom** Code Interpreter via `create_code_interpreter(name=..., execution_role_arn=..., network_configuration={"networkMode": "PUBLIC"})` and use its returned `codeInterpreterId` as the `identifier` passed to `start()`. Unverified whether `networkMode: PUBLIC` actually grants real internet egress for a custom interpreter (untested here) — should be spiked before Task 11 is implemented on this path.
   - **Option B** (lower-risk, no dependency on network config): vendor pytest (and any other needed test deps) as a wheel or source tree bundled with the deployment artifact, and `writeFiles`/`upload_files` it into the sandbox's site-packages (or a local directory added to `PYTHONPATH`) before invoking `python3 -m pytest`, avoiding any need for internet access inside the sandbox.
   - **Option C (untested — flagging for consideration, not recommending)**: if the default Sandbox network mode does in fact have limited-but-real reachability to AWS services (per AWS's docs), it may be possible to stay on the default identifier (no custom interpreter, no IAM execution role) and instead stage a pytest/mutmut wheel in an S3 bucket, then have the sandbox `pip install` directly from (or `curl`/download) that S3 object rather than from PyPI. This would sidestep both Option A's IAM/custom-interpreter overhead and Option B's need to embed the wheel bytes directly into the uploaded file set. Whether the default sandbox can actually reach S3 was not verified here and must be spiked before relying on it.
4. Task 11 should not assume "1 passed" style pytest output appears without first solving (3) — the trivial test in this spike never actually executed against a real pytest binary in the live sandbox.

## Conclusion

- SDK package: `bedrock-agentcore==1.21.0` (installed via `pip install bedrock-agentcore boto3` exactly as the brief's Step 1 specified — no package name change needed).
- Import path, class, and method names (`CodeInterpreter`, `.start()`, `.invoke("writeFiles", ...)`, `.invoke("executeCommand", ...)`, `.stop()`) all matched the brief exactly.
- `interpreter.stop()` succeeded cleanly on every run (no leaked sessions observed; each `stop()` call returned `True` with no exception).
- The live spike **did not achieve** "1 passed" from pytest — it failed at the `pip install pytest` step because PyPI/the public internet is unreachable from the default sandbox's network mode, a real infrastructure constraint the ADR did not account for. This is a genuine, reproducible finding (confirmed across three separate live sandbox sessions), not a bug in the spike script.
- This finding directly gates Task 11: `sandbox.py` needs (a) stream-consumption logic to read real command output (now reflected in the checked-in `scripts/spike_code_interpreter.py`), and (b) a strategy other than "pip install from PyPI inside the default sandbox" to get pytest into the environment — a custom interpreter with verified public network egress, vendoring pytest into the uploaded file set, or (untested) staging a wheel in S3 and pulling it from within the default sandbox if S3 turns out to be reachable there.
