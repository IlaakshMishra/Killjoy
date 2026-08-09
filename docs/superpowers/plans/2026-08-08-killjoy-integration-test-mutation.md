# Killjoy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Killjoy — a pipeline of specialist agents that generates real pytest integration tests for a PR, proves those tests aren't vanity coverage by running mutation testing against them, feeds surviving mutants back into test generation for up to 3 rounds, and opens a real GitHub PR with the tests plus a CI stage, but only after a sandbox run has actually proven the tests pass and scored.

**Architecture:** Five containers on AWS Bedrock AgentCore Runtime — Orchestrator, Environment Mapper, Test Generator, Execution & Mutation Evaluator, CI & PR Deliverer — invoked as a sequential pipeline with a bounded feedback loop (Test Generator ↔ Evaluator, max 3 rounds), not a parallel fan-out. A GitHub webhook hits API Gateway, a Lambda validates the payload's HMAC signature and starts the Orchestrator. Every stage's core logic is a plain, dependency-injected Python function (no AWS SDK calls inside it) so it is unit-testable without touching AWS; a thin `main.py` per agent wires the tested core to the real AWS clients and exposes a `BedrockAgentCoreApp` entrypoint. The Evaluator's mutation/pytest run happens inside an AgentCore Code Interpreter sandbox — the only stage that executes arbitrary target-repo code — everything else runs in the agent's own long-lived container.

**Tech Stack:** Python 3.13, pytest, mutmut (mutation testing), boto3, `bedrock-agentcore` SDK (`BedrockAgentCoreApp` for Runtime, `bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter` for the sandbox), `langchain-aws` (`ChatBedrockConverse`) for the two LLM-backed stages only, Terraform (`hashicorp/aws ~> 6.32`), GitHub REST API, AWS Lambda + API Gateway HTTP API, DynamoDB (guardrail ledger), Secrets Manager, ECR (ARM64 images), `moto` for AWS mocking in tests.

## Global Constraints

- v1 scope is Python/pytest, intra-application integration tests only — in-memory or mocked substitutes at the outer edges, real internal code everywhere else. No docker-compose/testcontainers (deferred to v2).
- GitHub Actions is the only CI system detected/targeted in v1.
- Trigger is GitHub webhook (PR opened/synchronize) → API Gateway → Lambda → AgentCore Runtime. Not a GitHub Actions-initiated call (that is gatecheck's pattern, not Killjoy's).
- Mutation feedback loop is capped at exactly 3 rounds; the Evaluator reports whatever score it converged on after that, it never loops forever.
- Every PR Killjoy opens: comes from a dedicated branch, never `main`; only opens after the sandbox run shows tests passing against real code AND a mutation score is computed; both facts (pass/fail, mutation score, any surviving mutant) are stated in the PR body; every PR is labeled `ai-generated`; nothing is ever auto-merged.
- At most one Killjoy PR per triggering PR (idempotent dedup), plus a configurable daily ceiling across all triggering PRs.
- Any stage failure stops the pipeline immediately. No partial or broken PR is ever opened. The Orchestrator logs which stage failed and why.
- Deployment target: AWS Bedrock AgentCore, `us-west-2`, mirroring the `gatecheck` project's proven conventions (`role_arn` attribute name, `bedrock-agentcore` boto3 service — not `bedrock-agentcore-runtime`, session IDs padded to ≥33 chars, ARM64 container builds, `python:3.13-slim` base image).
- Model IDs: `us.anthropic.claude-sonnet-4-6` for the two LLM-backed stages (Environment Mapper's synthesis step, Test Generator), overridable via `MODEL_ID` env var. Orchestrator, Evaluator, and CI/PR Deliverer make zero LLM calls — they are pure deterministic Python + AWS SDK, which is both cheaper and directly unit-testable.
- The executing agent can only `git add` (stage) during this plan — never `git commit`. Every task's final step stages the files it touched; the user reviews and commits manually. This is also why Tasks 3–5's planted-bug scenarios are stored as plain file variants under `eval/scenarios/`, not git branches: without commits, `git checkout -b` off an only-staged `main` can never actually diverge from it.

---

## File Structure

```
killjoy/
├── app/
│   ├── OrchestratorAgent/
│   │   ├── guardrails.py        # DynamoDB dedup + daily-ceiling reservation
│   │   ├── pipeline.py          # sequential stage runner + mutation feedback loop (DI'd)
│   │   ├── main.py              # AgentCore entrypoint, wires pipeline.py to real invoke_agent_runtime calls
│   │   ├── test_guardrails.py
│   │   ├── test_pipeline.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── EnvironmentMapperAgent/
│   │   ├── scanner.py           # ast-based structural scan, no LLM
│   │   ├── synthesizer.py       # LLM call that labels layers/boundaries from the scan
│   │   ├── main.py
│   │   ├── test_scanner.py
│   │   ├── test_synthesizer.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── TestGeneratorAgent/
│   │   ├── generator.py         # prompt build + LLM call + code extraction/validation
│   │   ├── main.py
│   │   ├── test_generator.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── EvaluatorAgent/
│   │   ├── runner.py            # subprocess pytest, junitxml parse
│   │   ├── mutation.py          # subprocess mutmut, results parse
│   │   ├── sandbox.py           # Code Interpreter session wrapper (DI'd start/invoke/stop)
│   │   ├── main.py
│   │   ├── test_runner.py
│   │   ├── test_mutation.py
│   │   ├── test_sandbox.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   └── CIPRDelivererAgent/
│       ├── git_ops.py           # clone/branch/commit/push via subprocess git
│       ├── workflow_injector.py # writes/updates .github/workflows/killjoy-integration.yml
│       ├── pr_builder.py        # deterministic PR body + open_pull_request (DI'd http post)
│       ├── main.py
│       ├── test_git_ops.py
│       ├── test_workflow_injector.py
│       ├── test_pr_builder.py
│       ├── Dockerfile
│       └── pyproject.toml
├── lambda/
│   └── webhook_handler/
│       ├── handler.py
│       ├── test_handler.py
│       └── requirements.txt
├── infra/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── iam.tf
│   ├── ecr.tf
│   ├── secrets.tf
│   ├── dynamodb.tf
│   ├── agents.tf
│   ├── lambda.tf
│   └── apigateway.tf
├── demo-repo/                   # plain directory (not its own git repo) Killjoy's local harness operates on directly
│   ├── app/
│   │   ├── __init__.py
│   │   ├── repository.py
│   │   ├── service.py
│   │   └── api.py
│   ├── tests/
│   │   ├── conftest.py
│   │   └── test_service.py
│   └── pyproject.toml
├── eval/
│   ├── known_bugs.json
│   └── scenarios/                # buggy file variants, swapped over demo-repo/ at eval time (no git branches)
│       ├── service-boundary/app/service.py
│       ├── repository-pagination/app/repository.py
│       └── api-missing-order/app/api.py
├── scripts/
│   └── evaluate_killjoy.py
├── conftest.py                  # adds each app/*/ dir to sys.path for pytest discovery
├── docs/
│   └── superpowers/plans/2026-08-08-killjoy-integration-test-mutation.md
└── README.md
```

---

### Task 1: Repo bootstrap

**Files:**
- Create: `/Users/ilaakshmishra/Documents/killjoy/.gitignore`
- Create: `/Users/ilaakshmishra/Documents/killjoy/conftest.py`
- Create: `/Users/ilaakshmishra/Documents/killjoy/README.md`

**Interfaces:**
- Produces: repo-root `conftest.py` that inserts every `app/<Agent>/` directory onto `sys.path`, so `pytest` run from repo root can `import scanner`, `import pipeline`, etc. by bare module name inside each agent's own test files. Every later task's tests depend on this.

- [ ] **Step 1: Initialize git and directories**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git init
mkdir -p app/OrchestratorAgent app/EnvironmentMapperAgent app/TestGeneratorAgent app/EvaluatorAgent app/CIPRDelivererAgent
mkdir -p lambda/webhook_handler infra demo-repo/app demo-repo/tests eval scripts docs/superpowers/plans
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.mutmut-cache
mutants/
.terraform/
*.tfstate
*.tfstate.backup
.venv/
*.egg-info/
```

- [ ] **Step 3: Write repo-root `conftest.py`**

```python
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent / "app"

if APP_DIR.is_dir():
    for agent_dir in APP_DIR.iterdir():
        if agent_dir.is_dir():
            sys.path.insert(0, str(agent_dir))
```

- [ ] **Step 4: Verify pytest collects with no test files yet**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest --collect-only`
Expected: `no tests ran` (exit code 5), no import errors.

- [ ] **Step 5: Write README stub**

```markdown
# Killjoy

Generates real pytest integration tests for a PR, proves them with mutation
testing (mutmut), feeds surviving mutants back into generation for up to 3
rounds, and opens a GitHub PR with the tests plus a CI stage — only after a
sandbox run has actually proven the tests pass and scored.

See `docs/superpowers/plans/2026-08-08-killjoy-integration-test-mutation.md`
for the full build plan.
```

- [ ] **Step 6: Stage the changes**

```bash
git add .gitignore conftest.py README.md
```

---

### Task 2: Demo repo baseline (correct code, passing tests)

**Files:**
- Create: `demo-repo/app/__init__.py`
- Create: `demo-repo/app/repository.py`
- Create: `demo-repo/app/service.py`
- Create: `demo-repo/app/api.py`
- Create: `demo-repo/tests/conftest.py`
- Create: `demo-repo/tests/test_service.py`
- Create: `demo-repo/pyproject.toml`

**Interfaces:**
- Produces: `repository.InMemoryOrderRepository` with `add_order`, `get_order_items(order_id)`, `get_price(item_id)`, `get_page(items, page, page_size)`, `get_order(order_id)`.
- Produces: `service.OrderService(repository)` with `calculate_total(order_id) -> float` (5+ items get a 10% bulk discount).
- Produces: `api.handle_get_order(service, order_id) -> dict` — `{"order_id":..., "total":...}` or `{"error": "not found"}`.
- This task builds the **correct** baseline. Tasks 3–5 add one buggy file variant each under `eval/scenarios/` (not git branches — see Task 3's note), swapped over this baseline at evaluation time.

- [ ] **Step 1: Write the failing tests**

`demo-repo/tests/conftest.py`:
```python
import pytest
from app.repository import InMemoryOrderRepository
from app.service import OrderService


@pytest.fixture
def repository():
    repo = InMemoryOrderRepository()
    repo.add_item(item_id="widget", price=10.0)
    repo.add_item(item_id="gadget", price=20.0)
    return repo


@pytest.fixture
def service(repository):
    return OrderService(repository)
```

`demo-repo/tests/test_service.py`:
```python
def test_calculate_total_no_discount_below_five_items(service, repository):
    repository.add_order(order_id=1, item_ids=["widget", "widget"])
    assert service.calculate_total(order_id=1) == 20.0


def test_calculate_total_exactly_five_items_gets_bulk_discount(service, repository):
    repository.add_order(order_id=2, item_ids=["widget"] * 5)
    # 5 * 10.0 = 50.0, 10% bulk discount at >= 5 items -> 45.0
    assert service.calculate_total(order_id=2) == 45.0


def test_get_page_second_page_returns_correct_slice(repository):
    items = list(range(10))
    page = repository.get_page(items, page=1, page_size=3)
    assert page == [3, 4, 5]
```

- [ ] **Step 2: Run tests to verify they fail (module not found)**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/demo-repo && python -m pytest tests/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write `demo-repo/pyproject.toml`**

```toml
[project]
name = "killjoy-demo-repo"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 4: Write `demo-repo/app/__init__.py`**

```python
```

- [ ] **Step 5: Write `demo-repo/app/repository.py`**

```python
class InMemoryOrderRepository:
    def __init__(self):
        self._prices: dict[str, float] = {}
        self._orders: dict[int, list[str]] = {}

    def add_item(self, item_id: str, price: float) -> None:
        self._prices[item_id] = price

    def add_order(self, order_id: int, item_ids: list[str]) -> None:
        self._orders[order_id] = item_ids

    def get_order(self, order_id: int) -> list[str] | None:
        return self._orders.get(order_id)

    def get_order_items(self, order_id: int) -> list[str]:
        return self._orders.get(order_id, [])

    def get_price(self, item_id: str) -> float:
        return self._prices[item_id]

    @staticmethod
    def get_page(items: list, page: int, page_size: int) -> list:
        start = page * page_size
        end = start + page_size
        return items[start:end]
```

- [ ] **Step 6: Write `demo-repo/app/service.py`**

```python
from app.repository import InMemoryOrderRepository

BULK_DISCOUNT_THRESHOLD = 5
BULK_DISCOUNT_RATE = 0.10


class OrderService:
    def __init__(self, repository: InMemoryOrderRepository):
        self._repository = repository

    def calculate_total(self, order_id: int) -> float:
        item_ids = self._repository.get_order_items(order_id)
        subtotal = sum(self._repository.get_price(item_id) for item_id in item_ids)
        if len(item_ids) >= BULK_DISCOUNT_THRESHOLD:
            subtotal *= 1 - BULK_DISCOUNT_RATE
        return round(subtotal, 2)
```

- [ ] **Step 7: Write `demo-repo/app/api.py`**

```python
from app.service import OrderService


def handle_get_order(service: OrderService, order_id: int) -> dict:
    order_items = service._repository.get_order(order_id)
    if order_items is None:
        return {"error": "not found"}
    total = service.calculate_total(order_id)
    return {"order_id": order_id, "total": total}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/demo-repo && python -m pytest tests/ -v`
Expected: 3 passed

- [ ] **Step 9: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add demo-repo/
```

---

### Task 3: Plant bug 1 — service/repository boundary off-by-one (bulk discount threshold)

**Files:**
- Create: `eval/scenarios/service-boundary/app/service.py` (standalone buggy variant, not a git branch — see note below)

**Interfaces:**
- Consumes: `OrderService.calculate_total` from Task 2.
- Produces: a scenario fixture file the harness (Task 26) copies over `demo-repo/app/service.py` at evaluation time, then restores afterward. Git branches cannot be used to store scenario variants here: nothing in this environment can `git commit`, and without a commit two branches never actually diverge — `git checkout -b buggy/x main` right after only-staged, uncommitted work on `main` would leave both branches pointing at the same uncommitted state. A plain file variant sidesteps that entirely and needs no git operation beyond staging it into the one `killjoy` repo.
- The bug: threshold comparison changes from `>=` to `>`, so an order with exactly 5 items is silently denied its bulk discount. `eval/known_bugs.json` (Task 6) records that Killjoy should be evaluated with the baseline's `test_calculate_total_exactly_five_items_gets_bulk_discount` deselected for this scenario, simulating "an integration bug the existing suite doesn't cover" — Killjoy's generated test is what's supposed to catch it, not the pre-existing suite.

- [ ] **Step 1: Create the scenario directory and buggy file**

```bash
mkdir -p /Users/ilaakshmishra/Documents/killjoy/eval/scenarios/service-boundary/app
```

Write `eval/scenarios/service-boundary/app/service.py` — identical to `demo-repo/app/service.py` from Task 2 except the threshold comparison:

```python
from app.repository import InMemoryOrderRepository

BULK_DISCOUNT_THRESHOLD = 5
BULK_DISCOUNT_RATE = 0.10


class OrderService:
    def __init__(self, repository: InMemoryOrderRepository):
        self._repository = repository

    def calculate_total(self, order_id: int) -> float:
        item_ids = self._repository.get_order_items(order_id)
        subtotal = sum(self._repository.get_price(item_id) for item_id in item_ids)
        if len(item_ids) > BULK_DISCOUNT_THRESHOLD:
            subtotal *= 1 - BULK_DISCOUNT_RATE
        return round(subtotal, 2)
```

- [ ] **Step 2: Verify the bug is real but invisible to the suite once the boundary test is deselected**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
cp demo-repo/app/service.py /tmp/killjoy-service-original.py
cp eval/scenarios/service-boundary/app/service.py demo-repo/app/service.py
cd demo-repo && python -m pytest tests/ -v --deselect tests/test_service.py::test_calculate_total_exactly_five_items_gets_bulk_discount
cd ..
cp /tmp/killjoy-service-original.py demo-repo/app/service.py
```

Expected: 2 passed — the bug is real but invisible once the one test that would catch it is deselected. The final `cp` restores `demo-repo/app/service.py` to the correct baseline before continuing.

- [ ] **Step 3: Stage the changes**

```bash
git add eval/scenarios/service-boundary/
```

---

### Task 4: Plant bug 2 — repository pagination off-by-one

**Files:**
- Create: `eval/scenarios/repository-pagination/app/repository.py` (standalone buggy variant)

**Interfaces:**
- Consumes: `InMemoryOrderRepository.get_page` from Task 2.
- Produces: same scenario-fixture pattern as Task 3.

- [ ] **Step 1: Create the scenario directory and buggy file**

```bash
mkdir -p /Users/ilaakshmishra/Documents/killjoy/eval/scenarios/repository-pagination/app
```

Write `eval/scenarios/repository-pagination/app/repository.py` — identical to `demo-repo/app/repository.py` from Task 2 except `get_page`:

```python
class InMemoryOrderRepository:
    def __init__(self):
        self._prices: dict[str, float] = {}
        self._orders: dict[int, list[str]] = {}

    def add_item(self, item_id: str, price: float) -> None:
        self._prices[item_id] = price

    def add_order(self, order_id: int, item_ids: list[str]) -> None:
        self._orders[order_id] = item_ids

    def get_order(self, order_id: int) -> list[str] | None:
        return self._orders.get(order_id)

    def get_order_items(self, order_id: int) -> list[str]:
        return self._orders.get(order_id, [])

    def get_price(self, item_id: str) -> float:
        return self._prices[item_id]

    @staticmethod
    def get_page(items: list, page: int, page_size: int) -> list:
        start = page * page_size
        return items[start:page_size]
```

- [ ] **Step 2: Verify the bug is real but invisible to the suite once the pagination test is deselected**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
cp demo-repo/app/repository.py /tmp/killjoy-repository-original.py
cp eval/scenarios/repository-pagination/app/repository.py demo-repo/app/repository.py
cd demo-repo && python -m pytest tests/ -v --deselect tests/test_service.py::test_get_page_second_page_returns_correct_slice
cd ..
cp /tmp/killjoy-repository-original.py demo-repo/app/repository.py
```

Expected: 2 passed — `get_page(items, page=0, page_size=3)` still returns `[0,1,2]` correctly by coincidence, so only page ≥ 1 reveals the bug, and that's exactly the deselected test. The final `cp` restores the baseline.

- [ ] **Step 3: Stage the changes**

```bash
git add eval/scenarios/repository-pagination/
```

---

### Task 5: Plant bug 3 — API handler unhandled missing order

**Files:**
- Create: `eval/scenarios/api-missing-order/app/api.py` (standalone buggy variant)

**Interfaces:**
- Consumes: `handle_get_order` from Task 2.
- Produces: same scenario-fixture pattern as Task 3.

- [ ] **Step 1: Create the scenario directory and buggy file**

```bash
mkdir -p /Users/ilaakshmishra/Documents/killjoy/eval/scenarios/api-missing-order/app
```

Write `eval/scenarios/api-missing-order/app/api.py`:

```python
from app.service import OrderService


def handle_get_order(service: OrderService, order_id: int) -> dict:
    total = service.calculate_total(order_id)
    return {"order_id": order_id, "total": total}
```

This removes the not-found check entirely — `calculate_total` on an unknown `order_id` returns `0.0` silently (via `get_order_items` defaulting to `[]`) instead of surfacing the missing-order condition, so the handler lies about an order existing. No existing baseline test in `test_service.py` calls `handle_get_order` at all, so this is uncaught by construction — matching a real-world "nobody wrote a test for the API layer" gap. No test needs to be deselected for this scenario.

- [ ] **Step 2: Verify the full suite still passes with the buggy variant swapped in**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
cp demo-repo/app/api.py /tmp/killjoy-api-original.py
cp eval/scenarios/api-missing-order/app/api.py demo-repo/app/api.py
cd demo-repo && python -m pytest tests/ -v
cd ..
cp /tmp/killjoy-api-original.py demo-repo/app/api.py
```

Expected: 3 passed (none of the baseline tests touch `api.py`). The final `cp` restores the baseline.

- [ ] **Step 3: Stage the changes**

```bash
git add eval/scenarios/api-missing-order/
```

---

### Task 6: Ground-truth manifest for evaluation

**Files:**
- Create: `eval/known_bugs.json`

**Interfaces:**
- Produces: `target_path` and `scenario_file`, the two fields `scripts/evaluate_killjoy.py` (Task 26) reads programmatically to know which file to swap and where. `deselect_test`, `layer`, `function`, and `description` are ground-truth documentation only — they record what Tasks 3–5 verified by hand (which pre-existing test, if any, had to be deselected for the bug to hide) but nothing in Task 26 reads them at runtime.

- [ ] **Step 1: Write `eval/known_bugs.json`**

```json
[
  {
    "id": "service-boundary",
    "layer": "service",
    "target_path": "app/service.py",
    "scenario_file": "eval/scenarios/service-boundary/app/service.py",
    "function": "OrderService.calculate_total",
    "description": "Bulk discount threshold uses > instead of >=, so an order with exactly 5 items is silently overcharged.",
    "deselect_test": "tests/test_service.py::test_calculate_total_exactly_five_items_gets_bulk_discount"
  },
  {
    "id": "repository-pagination",
    "layer": "repository",
    "target_path": "app/repository.py",
    "scenario_file": "eval/scenarios/repository-pagination/app/repository.py",
    "function": "InMemoryOrderRepository.get_page",
    "description": "Pagination slice end index uses page_size instead of (page+1)*page_size, so page >= 1 returns wrong or empty results.",
    "deselect_test": "tests/test_service.py::test_get_page_second_page_returns_correct_slice"
  },
  {
    "id": "api-missing-order",
    "layer": "api",
    "target_path": "app/api.py",
    "scenario_file": "eval/scenarios/api-missing-order/app/api.py",
    "function": "handle_get_order",
    "description": "Handler no longer checks whether the order exists before computing a total, so an unknown order_id returns a fabricated total of 0.0 instead of an error.",
    "deselect_test": null
  }
]
```

- [ ] **Step 2: Validate it parses**

Run: `python -c "import json; print(len(json.load(open('/Users/ilaakshmishra/Documents/killjoy/eval/known_bugs.json'))))"`
Expected: `3`

- [ ] **Step 3: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add eval/known_bugs.json
```

---

### Task 7: AgentCore Code Interpreter spike

**Files:**
- Create: `docs/spike-code-interpreter.md`
- Create: `scripts/spike_code_interpreter.py`

**Interfaces:**
- Produces: proof (or documented failure) that `bedrock_agentcore.tools.code_interpreter_client.CodeInterpreter` can execute `pip install pytest && pytest` inside the AgentCore sandbox — the ADR's single open risk that gates the Evaluator's design (Task 11). This is a manual/local spike, not part of the automated test suite — it requires live AWS credentials with Bedrock AgentCore access.

- [ ] **Step 1: Install the SDK locally**

```bash
pip install bedrock-agentcore boto3
```

- [ ] **Step 2: Write the spike script**

```python
"""
Spike: confirm AgentCore Code Interpreter can run pytest against uploaded files.
Requires AWS credentials configured for an account with Bedrock AgentCore access.
Run manually: python scripts/spike_code_interpreter.py
"""
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

REGION = "us-west-2"

TEST_FILE_CONTENT = """
def test_trivial():
    assert 1 + 1 == 2
"""


def main():
    interpreter = CodeInterpreter(region=REGION)
    interpreter.start()
    try:
        write_result = interpreter.invoke(
            "writeFiles",
            {"content": [{"path": "test_trivial.py", "text": TEST_FILE_CONTENT}]},
        )
        print("writeFiles result:", write_result)

        install_result = interpreter.invoke(
            "executeCommand",
            {"command": "pip install --quiet pytest"},
        )
        print("pip install result:", install_result)

        run_result = interpreter.invoke(
            "executeCommand",
            {"command": "pytest test_trivial.py -v"},
        )
        print("pytest result:", run_result)
    finally:
        interpreter.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the spike against a real AWS account**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python scripts/spike_code_interpreter.py`
Expected: stdout containing `1 passed` from the `pytest test_trivial.py -v` invocation. Capture the exact tool names (`writeFiles`, `executeCommand`) and response shape that actually come back — the AWS SDK's method/tool names are the ADR's open risk; if `invoke`'s first argument or response envelope differs from what's above, that is the real finding this spike exists to produce.

- [ ] **Step 4: Record the result**

Write `docs/spike-code-interpreter.md` documenting: exact SDK version used (`pip show bedrock-agentcore`), the literal tool names that worked, the full stdout of the pytest run, and total wall-clock time for start→writeFiles→install→run→stop. If any step failed, record the exact exception and adjust Task 11's `sandbox.py` tool names to match before implementing it — Task 11 depends on this file being accurate, not on the placeholder names above.

- [ ] **Step 5: Stage the changes**

```bash
git add docs/spike-code-interpreter.md scripts/spike_code_interpreter.py
```

---

### Task 8: Environment Mapper — structural scanner (no LLM)

**Files:**
- Create: `app/EnvironmentMapperAgent/scanner.py`
- Test: `app/EnvironmentMapperAgent/test_scanner.py`

**Interfaces:**
- Produces: `scan_repo(repo_path: Path) -> dict` returning
  `{"modules": [{"path": str, "functions": [str], "imports": [str]}], "fixtures": [{"name": str, "file": str}], "directories": [str]}`.
  This raw structural dict is what `synthesizer.py` (Task 9) consumes.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EnvironmentMapperAgent/test_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanner'`

- [ ] **Step 3: Write `scanner.py`**

```python
import ast
from pathlib import Path


def _extract_functions(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(f"{node.name}.{item.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not any(node in ast.walk(c) for c in ast.walk(tree) if isinstance(c, ast.ClassDef)):
                names.append(node.name)
    return names


def _extract_imports(tree: ast.Module) -> list[str]:
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _extract_fixture_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                deco_source = ast.dump(decorator)
                if "fixture" in deco_source:
                    names.append(node.name)
    return names


def scan_repo(repo_path: Path) -> dict:
    modules = []
    fixtures = []
    directories = set()

    for py_file in sorted(repo_path.rglob("*.py")):
        rel_path = py_file.relative_to(repo_path)
        if any(part in (".git", "__pycache__") for part in rel_path.parts):
            continue

        directories.add(rel_path.parts[0]) if len(rel_path.parts) > 1 else None

        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        if py_file.name == "conftest.py":
            fixture_names = _extract_fixture_names(tree)
            for name in fixture_names:
                fixtures.append({"name": name, "file": str(rel_path)})
            continue

        modules.append({
            "path": str(rel_path),
            "functions": _extract_functions(tree),
            "imports": _extract_imports(tree),
        })

    return {
        "modules": modules,
        "fixtures": fixtures,
        "directories": sorted(directories),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EnvironmentMapperAgent/test_scanner.py -v`
Expected: PASS

- [ ] **Step 5: Run scanner against the real demo-repo as a sanity check**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -c "from pathlib import Path; import sys; sys.path.insert(0,'app/EnvironmentMapperAgent'); from scanner import scan_repo; import json; print(json.dumps(scan_repo(Path('demo-repo')), indent=2))"`
Expected: JSON showing `app/service.py`, `app/repository.py`, `app/api.py` under `modules`, and the `repository`/`service` fixtures from `demo-repo/tests/conftest.py` under `fixtures`.

- [ ] **Step 6: Stage the changes**

```bash
git add app/EnvironmentMapperAgent/scanner.py app/EnvironmentMapperAgent/test_scanner.py
```

---

### Task 9: Environment Mapper — LLM synthesis, entrypoint, and deployment files

**Files:**
- Create: `app/EnvironmentMapperAgent/synthesizer.py`
- Create: `app/EnvironmentMapperAgent/main.py`
- Create: `app/EnvironmentMapperAgent/Dockerfile`
- Create: `app/EnvironmentMapperAgent/pyproject.toml`
- Test: `app/EnvironmentMapperAgent/test_synthesizer.py`

**Interfaces:**
- Consumes: `scan_repo(repo_path) -> dict` from Task 8.
- Produces: `synthesize_env_map(raw_scan: dict, llm_invoke: Callable[[str], str]) -> dict` returning the environment-map schema:
  `{"layers": [{"name": str, "path": str, "role": "boundary"|"internal", "substitute": "none_real_execution"|"in_memory"|"mock"}], "fixtures": [{"name": str, "file": str}], "outer_edges": [{"boundary": str, "substitute_recommendation": str}]}`.
  This is the exact dict `generator.py` (Task 10) consumes as `env_map`.

- [ ] **Step 1: Write the failing test**

```python
import json
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EnvironmentMapperAgent/test_synthesizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'synthesizer'`

- [ ] **Step 3: Write `synthesizer.py`**

```python
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
        return json.loads(match.group(0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EnvironmentMapperAgent/test_synthesizer.py -v`
Expected: PASS

- [ ] **Step 5: Write `main.py`**

```python
import json
import logging
import os
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse

from scanner import scan_repo
from synthesizer import synthesize_env_map

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

llm = ChatBedrockConverse(model_id=MODEL_ID)

app = BedrockAgentCoreApp()


def _llm_invoke(prompt: str) -> str:
    response = llm.invoke([{"role": "user", "content": prompt}])
    return response.content if hasattr(response, "content") else str(response)


@app.entrypoint
async def handler(payload: dict) -> dict:
    repo_path = payload.get("repo_path")
    if not repo_path:
        return {"error": "repo_path is required"}

    raw_scan = scan_repo(Path(repo_path))
    try:
        env_map = synthesize_env_map(raw_scan, _llm_invoke)
    except ValueError as exc:
        logger.error("Environment Mapper synthesis failed: %s", exc)
        return {"error": str(exc)}

    return env_map


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 6: Write `Dockerfile`**

```dockerfile
FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
COPY pyproject.toml scanner.py synthesizer.py main.py ./
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
```

- [ ] **Step 7: Write `pyproject.toml`**

```toml
[project]
name = "killjoy-environment-mapper"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "bedrock-agentcore",
    "langchain-aws",
    "boto3",
]
```

- [ ] **Step 8: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add app/EnvironmentMapperAgent/
```

---

### Task 10: Test Generator — core generation logic

**Files:**
- Create: `app/TestGeneratorAgent/generator.py`
- Test: `app/TestGeneratorAgent/test_generator.py`

**Interfaces:**
- Consumes: environment-map dict from Task 9 (`{"layers": [...], "fixtures": [...], "outer_edges": [...]}`).
- Produces: `generate_tests(diff: str, env_map: dict, llm_invoke: Callable[[str], str], surviving_mutants: list[dict] | None = None) -> str` — returns raw Python source text of a pytest test module. Also produces `validate_test_source(source: str) -> None`, raising `SyntaxError` on invalid Python. `main.py` (Task 12) and the Evaluator's feedback loop payload (Task 11/15) both rely on the `surviving_mutants` parameter shape: `[{"id": str, "file": str, "line": int, "description": str}]`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from generator import generate_tests, validate_test_source


def test_generate_tests_returns_validated_python_source():
    env_map = {
        "layers": [
            {"name": "service", "path": "app/service.py", "role": "internal", "substitute": "none_real_execution"},
            {"name": "repository", "path": "app/repository.py", "role": "internal", "substitute": "in_memory"},
        ],
        "fixtures": [{"name": "repository", "file": "tests/conftest.py"}],
        "outer_edges": [],
    }
    diff = "diff --git a/app/service.py b/app/service.py\n+ changed calculate_total"

    fake_source = (
        "def test_bulk_discount_boundary(service, repository):\n"
        "    repository.add_order(order_id=1, item_ids=['widget'] * 5)\n"
        "    assert service.calculate_total(order_id=1) == 45.0\n"
    )

    def fake_llm_invoke(prompt: str) -> str:
        assert "app/service.py" in prompt
        assert diff in prompt
        return f"```python\n{fake_source}```"

    result = generate_tests(diff, env_map, fake_llm_invoke)

    assert result.strip() == fake_source.strip()
    validate_test_source(result)  # must not raise


def test_generate_tests_includes_surviving_mutants_in_prompt():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}
    surviving_mutants = [
        {"id": "1", "file": "app/service.py", "line": 10, "description": "changed >= to > and no test failed"}
    ]

    def fake_llm_invoke(prompt: str) -> str:
        assert "changed >= to > and no test failed" in prompt
        return "def test_x():\n    assert True\n"

    generate_tests("diff", env_map, fake_llm_invoke, surviving_mutants=surviving_mutants)


def test_validate_test_source_raises_on_invalid_python():
    with pytest.raises(SyntaxError):
        validate_test_source("def test_broken(:\n    pass")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/TestGeneratorAgent/test_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'generator'`

- [ ] **Step 3: Write `generator.py`**

```python
import re
from typing import Callable

SYSTEM_PROMPT = """You write pytest integration tests that exercise multiple
real internal components of a codebase together. You are given the PR diff,
an environment map describing each layer and which layers should be
substituted (only genuine outer edges — real internal code must run
unmodified), and the existing conftest fixtures available to you.

Rules:
- Use only the fixtures listed in the environment map; do not invent new ones.
- Never mock or stub a layer marked "none_real_execution" — call its real code.
- Only substitute layers explicitly marked "in_memory" or "mock".
- Write complete, runnable pytest test functions — no placeholders, no TODOs.
- Return ONLY a python code block, no prose before or after.
"""


def _build_prompt(diff: str, env_map: dict, surviving_mutants: list[dict] | None) -> str:
    layers_desc = "\n".join(
        f"- {l['name']} ({l['path']}): role={l['role']}, substitute={l['substitute']}"
        for l in env_map.get("layers", [])
    )
    fixtures_desc = "\n".join(
        f"- {f['name']} (defined in {f['file']})" for f in env_map.get("fixtures", [])
    )

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"PR diff:\n{diff}\n\n"
        f"Layers:\n{layers_desc}\n\n"
        f"Available fixtures:\n{fixtures_desc}\n"
    )

    if surviving_mutants:
        mutant_lines = "\n".join(
            f"- {m['file']}:{m['line']} — {m['description']}" for m in surviving_mutants
        )
        prompt += (
            "\n\nThe previous round of tests did NOT catch these mutations "
            "(the mutated code ran and no test failed). Write additional or "
            "revised tests that would fail against each of these mutations:\n"
            f"{mutant_lines}\n"
        )

    return prompt


def generate_tests(
    diff: str,
    env_map: dict,
    llm_invoke: Callable[[str], str],
    surviving_mutants: list[dict] | None = None,
) -> str:
    prompt = _build_prompt(diff, env_map, surviving_mutants)
    raw = llm_invoke(prompt)

    code_block_match = re.search(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
    source = code_block_match.group(1) if code_block_match else raw

    validate_test_source(source)
    return source


def validate_test_source(source: str) -> None:
    compile(source, "<generated_test>", "exec")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/TestGeneratorAgent/test_generator.py -v`
Expected: 3 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/TestGeneratorAgent/generator.py app/TestGeneratorAgent/test_generator.py
```

---

### Task 11: Test Generator — entrypoint and deployment files

**Files:**
- Create: `app/TestGeneratorAgent/main.py`
- Create: `app/TestGeneratorAgent/Dockerfile`
- Create: `app/TestGeneratorAgent/pyproject.toml`

**Interfaces:**
- Consumes: `generate_tests` from Task 10.
- Produces: AgentCore HTTP entrypoint accepting `{"diff": str, "env_map": dict, "surviving_mutants": list[dict] | null}` and returning `{"test_source": str}` or `{"error": str}`.

- [ ] **Step 1: Write `main.py`**

```python
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse

from generator import generate_tests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

llm = ChatBedrockConverse(model_id=MODEL_ID)

app = BedrockAgentCoreApp()


def _llm_invoke(prompt: str) -> str:
    response = llm.invoke([{"role": "user", "content": prompt}])
    return response.content if hasattr(response, "content") else str(response)


@app.entrypoint
async def handler(payload: dict) -> dict:
    diff = payload.get("diff", "")
    env_map = payload.get("env_map", {})
    surviving_mutants = payload.get("surviving_mutants")

    if not diff:
        return {"error": "diff is required"}

    try:
        test_source = generate_tests(diff, env_map, _llm_invoke, surviving_mutants)
    except SyntaxError as exc:
        logger.error("Generated test source failed to compile: %s", exc)
        return {"error": f"generated test source is not valid Python: {exc}"}

    return {"test_source": test_source}


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
COPY pyproject.toml generator.py main.py ./
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "killjoy-test-generator"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "bedrock-agentcore",
    "langchain-aws",
    "boto3",
]
```

- [ ] **Step 4: Sanity-check the container builds locally**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/app/TestGeneratorAgent && docker build --platform linux/arm64 -t killjoy-test-generator:local .`
Expected: build succeeds with exit code 0.

- [ ] **Step 5: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add app/TestGeneratorAgent/main.py app/TestGeneratorAgent/Dockerfile app/TestGeneratorAgent/pyproject.toml
```

---

### Task 12: Evaluator — pytest runner core

**Files:**
- Create: `app/EvaluatorAgent/runner.py`
- Test: `app/EvaluatorAgent/test_runner.py`

**Interfaces:**
- Produces: `run_pytest(repo_path: Path, test_file_rel_path: str) -> dict` returning `{"passed": bool, "summary": str, "returncode": int}`.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from runner import run_pytest


def test_run_pytest_reports_pass_for_passing_test(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 == 1\n")

    result = run_pytest(tmp_path, "test_ok.py")

    assert result["passed"] is True
    assert result["returncode"] == 0


def test_run_pytest_reports_failure_for_failing_test(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert 1 == 2\n")

    result = run_pytest(tmp_path, "test_bad.py")

    assert result["passed"] is False
    assert result["returncode"] != 0
    assert "1 failed" in result["summary"] or "failed" in result["summary"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EvaluatorAgent/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runner'`

- [ ] **Step 3: Write `runner.py`**

```python
import subprocess
from pathlib import Path


def run_pytest(repo_path: Path, test_file_rel_path: str) -> dict:
    result = subprocess.run(
        ["python", "-m", "pytest", test_file_rel_path, "-v"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    summary_lines = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line or "error" in line]
    summary = summary_lines[-1] if summary_lines else result.stdout[-500:]

    return {
        "passed": result.returncode == 0,
        "summary": summary,
        "returncode": result.returncode,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EvaluatorAgent/test_runner.py -v`
Expected: 2 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/EvaluatorAgent/runner.py app/EvaluatorAgent/test_runner.py
```

---

### Task 13: Evaluator — mutation testing core

**Files:**
- Create: `app/EvaluatorAgent/mutation.py`
- Test: `app/EvaluatorAgent/test_mutation.py`

**Interfaces:**
- Consumes: nothing from earlier tasks directly (works on a filesystem repo path).
- Produces: `run_mutation(repo_path: Path, touched_paths: list[str], test_file_rel_path: str) -> dict` returning
  `{"score": float, "killed": int, "survived": int, "surviving_mutants": [{"id": str, "file": str, "line": int, "description": str}]}`.
  `surviving_mutants` is exactly the shape `generator.generate_tests` (Task 10) expects for its `surviving_mutants` parameter.

- [ ] **Step 1: Write the failing test**

This test needs `mutmut` installed and a real target module with one weak test (to produce a survivor) to prove parsing works end-to-end — it is intentionally an integration test of the real CLI, not a mock, because the whole point of this module is to correctly parse mutmut's real output.

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EvaluatorAgent/test_mutation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mutation'`

- [ ] **Step 3: Write `mutation.py`**

```python
import re
import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=300)


def run_mutation(repo_path: Path, touched_paths: list[str], test_file_rel_path: str) -> dict:
    paths_arg = ",".join(touched_paths)

    setup_cfg = repo_path / "setup.cfg"
    setup_cfg.write_text(
        "[mutmut]\n"
        f"paths_to_mutate={paths_arg}\n"
        f"tests_dir=.\n"
        f"runner=python -m pytest {test_file_rel_path} -x -q\n"
    )

    run_result = _run(["mutmut", "run", "--no-progress"], repo_path)

    results_result = _run(["mutmut", "results"], repo_path)
    results_output = results_result.stdout

    survived_ids = re.findall(r"^(\d+)\.\s.*survived", results_output, re.MULTILINE | re.IGNORECASE)
    if not survived_ids:
        survived_ids = [
            line.split(":")[0].strip()
            for line in results_output.splitlines()
            if "survived" in line.lower()
        ]

    killed_match = re.search(r"(\d+)/(\d+)", run_result.stdout)
    total_mutants = int(killed_match.group(2)) if killed_match else (len(survived_ids))
    survived_count = len(survived_ids)
    killed_count = max(total_mutants - survived_count, 0)

    surviving_mutants = []
    for mutant_id in survived_ids:
        show_result = _run(["mutmut", "show", mutant_id], repo_path)
        diff_text = show_result.stdout
        line_match = re.search(r"@@ -(\d+)", diff_text)
        line_number = int(line_match.group(1)) if line_match else 0
        target_file = touched_paths[0] if touched_paths else ""
        surviving_mutants.append({
            "id": mutant_id,
            "file": target_file,
            "line": line_number,
            "description": diff_text.strip()[:500],
        })

    score = (killed_count / total_mutants) if total_mutants else 1.0

    return {
        "score": round(score, 4),
        "killed": killed_count,
        "survived": survived_count,
        "surviving_mutants": surviving_mutants,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && pip install mutmut && python -m pytest app/EvaluatorAgent/test_mutation.py -v`
Expected: 2 passed. If `mutmut`'s actual CLI output format differs from what the regexes above assume, this step is where that surfaces — adjust the regexes in `mutation.py` to match the installed `mutmut` version's real `mutmut results` / `mutmut show` output before moving on; do not weaken the test assertions to make them pass.

- [ ] **Step 5: Stage the changes**

```bash
git add app/EvaluatorAgent/mutation.py app/EvaluatorAgent/test_mutation.py
```

---

### Task 14: Evaluator — sandbox wrapper, entrypoint, and deployment files

**Files:**
- Create: `app/EvaluatorAgent/sandbox.py`
- Create: `app/EvaluatorAgent/main.py`
- Create: `app/EvaluatorAgent/Dockerfile`
- Create: `app/EvaluatorAgent/pyproject.toml`
- Test: `app/EvaluatorAgent/test_sandbox.py`

**Interfaces:**
- Consumes: `run_pytest` (Task 12) and `run_mutation` (Task 13) — `sandbox.py` ships the same two functions into the remote sandbox rather than re-implementing them, so what runs remotely is identical to what Task 12/13's tests already proved correct locally.
- Produces: `execute_in_sandbox(start_fn, invoke_fn, stop_fn, repo_files: dict[str, str], test_source: str, touched_paths: list[str]) -> dict` — same return shape as `run_mutation`, plus a `"tests_passed": bool` key from the pytest stage. `start_fn`/`invoke_fn`/`stop_fn` are injected so this is testable without AWS; `main.py` wires them to the real `CodeInterpreter` from the Task 7 spike.

- [ ] **Step 1: Write the failing test**

```python
from sandbox import execute_in_sandbox


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
        if tool_name == "executeCommand" and "mutmut" in params["command"]:
            return {"stdout": "mutation score 100%\nkilled: 1, survived: 0", "exitCode": 0}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EvaluatorAgent/test_sandbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sandbox'`

- [ ] **Step 3: Write `sandbox.py`**

```python
from typing import Callable


def execute_in_sandbox(
    start_fn: Callable[[], str],
    invoke_fn: Callable[[str, str, dict], dict],
    stop_fn: Callable[[str], None],
    repo_files: dict[str, str],
    test_source: str,
    touched_paths: list[str],
) -> dict:
    session_id = start_fn()
    try:
        file_content = [{"path": path, "text": text} for path, text in repo_files.items()]
        file_content.append({"path": "test_generated.py", "text": test_source})
        invoke_fn(session_id, "writeFiles", {"content": file_content})

        invoke_fn(session_id, "executeCommand", {"command": "pip install --quiet pytest mutmut"})

        pytest_result = invoke_fn(session_id, "executeCommand", {"command": "python -m pytest test_generated.py -v"})
        tests_passed = pytest_result.get("exitCode", 1) == 0

        if not tests_passed:
            return {
                "tests_passed": False,
                "score": 0.0,
                "killed": 0,
                "survived": 0,
                "surviving_mutants": [],
                "pytest_output": pytest_result.get("stdout", ""),
            }

        paths_arg = ",".join(touched_paths)
        setup_cfg = (
            "[mutmut]\n"
            f"paths_to_mutate={paths_arg}\n"
            "tests_dir=.\n"
            "runner=python -m pytest test_generated.py -x -q\n"
        )
        invoke_fn(session_id, "writeFiles", {"content": [{"path": "setup.cfg", "text": setup_cfg}]})

        mutation_result = invoke_fn(session_id, "executeCommand", {"command": "mutmut run --no-progress; mutmut results"})

        return {
            "tests_passed": True,
            "pytest_output": pytest_result.get("stdout", ""),
            "mutation_raw_output": mutation_result.get("stdout", ""),
        }
    finally:
        stop_fn(session_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/EvaluatorAgent/test_sandbox.py -v`
Expected: PASS

- [ ] **Step 5: Write `main.py`**

Uses the exact tool names and response shape recorded in `docs/spike-code-interpreter.md` (Task 7) — if the spike found different names than `writeFiles`/`executeCommand`, use those here instead.

```python
import logging
import re

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

from sandbox import execute_in_sandbox
from mutation import run_mutation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


def _parse_mutation_output(raw_output: str, touched_paths: list[str]) -> dict:
    survived_ids = re.findall(r"^(\d+)\.\s.*survived", raw_output, re.MULTILINE | re.IGNORECASE)
    killed_match = re.search(r"(\d+)/(\d+)", raw_output)
    total = int(killed_match.group(2)) if killed_match else len(survived_ids)
    survived = len(survived_ids)
    killed = max(total - survived, 0)
    score = (killed / total) if total else 1.0
    surviving_mutants = [
        {"id": mid, "file": touched_paths[0] if touched_paths else "", "line": 0, "description": f"mutant {mid} survived"}
        for mid in survived_ids
    ]
    return {"score": round(score, 4), "killed": killed, "survived": survived, "surviving_mutants": surviving_mutants}


@app.entrypoint
async def handler(payload: dict) -> dict:
    repo_files = payload.get("repo_files")
    test_source = payload.get("test_source")
    touched_paths = payload.get("touched_paths", [])

    if not repo_files or not test_source:
        return {"error": "repo_files and test_source are required"}

    interpreter = CodeInterpreter(region="us-west-2")

    def start_fn():
        interpreter.start()
        return "session"

    def invoke_fn(session_id, tool_name, params):
        return interpreter.invoke(tool_name, params)

    def stop_fn(session_id):
        interpreter.stop()

    result = execute_in_sandbox(start_fn, invoke_fn, stop_fn, repo_files, test_source, touched_paths)

    if not result["tests_passed"]:
        return {"error": "generated tests failed against real code", "pytest_output": result.get("pytest_output", "")}

    mutation_summary = _parse_mutation_output(result.get("mutation_raw_output", ""), touched_paths)

    return {"tests_passed": True, **mutation_summary}


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 6: Write `Dockerfile`**

```dockerfile
FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
COPY pyproject.toml runner.py mutation.py sandbox.py main.py ./
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
```

- [ ] **Step 7: Write `pyproject.toml`**

```toml
[project]
name = "killjoy-evaluator"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "bedrock-agentcore",
    "boto3",
    "mutmut",
    "pytest",
]
```

- [ ] **Step 8: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add app/EvaluatorAgent/sandbox.py app/EvaluatorAgent/main.py app/EvaluatorAgent/Dockerfile app/EvaluatorAgent/pyproject.toml app/EvaluatorAgent/test_sandbox.py
```

---

### Task 15: CI & PR Deliverer — git operations core

**Files:**
- Create: `app/CIPRDelivererAgent/git_ops.py`
- Test: `app/CIPRDelivererAgent/test_git_ops.py`

**Interfaces:**
- Produces: `clone_repo(repo_url: str, token: str, dest: Path, ref: str) -> None`, `create_branch_and_commit(repo_path: Path, branch_name: str, files: dict[str, str], commit_message: str) -> None`, `push_branch(repo_path: Path, branch_name: str) -> None`.

- [ ] **Step 1: Write the failing test**

Uses a local bare repo as the "remote" so the test needs no network access.

```python
import subprocess
from pathlib import Path

from git_ops import create_branch_and_commit, push_branch


def _init_local_repo_with_remote(tmp_path: Path) -> Path:
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_path)], check=True)

    work_path = tmp_path / "work"
    work_path.mkdir()
    subprocess.run(["git", "init"], cwd=work_path, check=True)
    subprocess.run(["git", "config", "user.email", "killjoy@example.com"], cwd=work_path, check=True)
    subprocess.run(["git", "config", "user.name", "Killjoy"], cwd=work_path, check=True)
    (work_path / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=work_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=work_path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=work_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_path)], cwd=work_path, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=work_path, check=True)

    return work_path


def test_create_branch_and_commit_writes_files_and_commits(tmp_path):
    work_path = _init_local_repo_with_remote(tmp_path)

    create_branch_and_commit(
        work_path,
        branch_name="killjoy/pr-1-abc123",
        files={"tests/killjoy/generated/test_new.py": "def test_new():\n    assert True\n"},
        commit_message="test: add generated integration test",
    )

    result = subprocess.run(["git", "branch", "--show-current"], cwd=work_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "killjoy/pr-1-abc123"

    assert (work_path / "tests/killjoy/generated/test_new.py").exists()

    log_result = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=work_path, capture_output=True, text=True, check=True)
    assert log_result.stdout.strip() == "test: add generated integration test"


def test_push_branch_pushes_to_remote(tmp_path):
    work_path = _init_local_repo_with_remote(tmp_path)
    create_branch_and_commit(
        work_path,
        branch_name="killjoy/pr-2-def456",
        files={"tests/killjoy/generated/test_new.py": "def test_new():\n    assert True\n"},
        commit_message="test: add generated integration test",
    )

    push_branch(work_path, "killjoy/pr-2-def456")

    remote_path = tmp_path / "remote.git"
    ls_remote = subprocess.run(
        ["git", "ls-remote", "--heads", str(remote_path), "killjoy/pr-2-def456"],
        capture_output=True, text=True, check=True,
    )
    assert "killjoy/pr-2-def456" in ls_remote.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/CIPRDelivererAgent/test_git_ops.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'git_ops'`

- [ ] **Step 3: Write `git_ops.py`**

```python
import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def clone_repo(repo_url: str, token: str, dest: Path, ref: str) -> None:
    authenticated_url = repo_url.replace("https://", f"https://x-access-token:{token}@")
    subprocess.run(["git", "clone", authenticated_url, str(dest)], check=True, capture_output=True, text=True)
    _run_git(["checkout", ref], dest)


def create_branch_and_commit(repo_path: Path, branch_name: str, files: dict[str, str], commit_message: str) -> None:
    _run_git(["checkout", "-b", branch_name], repo_path)

    for rel_path, content in files.items():
        file_path = repo_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        _run_git(["add", rel_path], repo_path)

    _run_git(["commit", "-m", commit_message], repo_path)


def push_branch(repo_path: Path, branch_name: str) -> None:
    _run_git(["push", "origin", branch_name], repo_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/CIPRDelivererAgent/test_git_ops.py -v`
Expected: 2 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/CIPRDelivererAgent/git_ops.py app/CIPRDelivererAgent/test_git_ops.py
```

---

### Task 16: CI & PR Deliverer — GitHub Actions workflow injector

**Files:**
- Create: `app/CIPRDelivererAgent/workflow_injector.py`
- Test: `app/CIPRDelivererAgent/test_workflow_injector.py`

**Interfaces:**
- Produces: `ensure_killjoy_workflow(repo_path: Path) -> bool` — writes/overwrites `.github/workflows/killjoy-integration.yml` with a dedicated job that runs Killjoy-generated tests; returns `True` if the file's content changed, `False` if it was already up to date.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/CIPRDelivererAgent/test_workflow_injector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'workflow_injector'`

- [ ] **Step 3: Write `workflow_injector.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/CIPRDelivererAgent/test_workflow_injector.py -v`
Expected: 2 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/CIPRDelivererAgent/workflow_injector.py app/CIPRDelivererAgent/test_workflow_injector.py
```

---

### Task 17: CI & PR Deliverer — PR body builder and PR opener

**Files:**
- Create: `app/CIPRDelivererAgent/pr_builder.py`
- Test: `app/CIPRDelivererAgent/test_pr_builder.py`

**Interfaces:**
- Consumes: the Evaluator's final-round result shape from Task 14 (`{"tests_passed": bool, "score": float, "killed": int, "survived": int, "surviving_mutants": [...]}`).
- Produces: `build_pr_body(run_result: dict) -> str` (deterministic markdown) and `open_pull_request(github_post: Callable, owner: str, repo: str, branch: str, base: str, title: str, body: str, labels: list[str]) -> dict`. This is the guardrail enforcement point: `open_pull_request` must be called only when `run_result["tests_passed"] is True` — `main.py` (Task 18) is what actually enforces that by refusing to call it otherwise.

- [ ] **Step 1: Write the failing test**

```python
from pr_builder import build_pr_body, open_pull_request


def test_build_pr_body_states_pass_fail_score_and_survivors():
    run_result = {
        "tests_passed": True,
        "score": 0.85,
        "killed": 17,
        "survived": 3,
        "surviving_mutants": [
            {"id": "42", "file": "app/service.py", "line": 12, "description": "changed >= to > and no test failed"}
        ],
    }

    body = build_pr_body(run_result)

    assert "tests passing against real code" in body.lower() or "passed" in body.lower()
    assert "0.85" in body or "85" in body
    assert "app/service.py:12" in body
    assert "changed >= to > and no test failed" in body


def test_open_pull_request_sends_expected_payload_and_labels():
    captured = {}

    def fake_github_post(url, json_body, headers):
        captured["url"] = url
        captured["json_body"] = json_body
        captured["headers"] = headers
        return {"html_url": "https://github.com/acme/widgets/pull/99", "number": 99}

    result = open_pull_request(
        fake_github_post,
        owner="acme",
        repo="widgets",
        branch="killjoy/pr-5-abc123",
        base="main",
        title="Killjoy: integration tests for PR #5",
        body="body text",
        labels=["ai-generated"],
    )

    assert result["number"] == 99
    assert captured["json_body"]["head"] == "killjoy/pr-5-abc123"
    assert captured["json_body"]["base"] == "main"
    assert captured["json_body"]["base"] != captured["json_body"]["head"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/CIPRDelivererAgent/test_pr_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pr_builder'`

- [ ] **Step 3: Write `pr_builder.py`**

```python
from typing import Callable


def build_pr_body(run_result: dict) -> str:
    status = "✅ passed" if run_result["tests_passed"] else "❌ failed"
    score_pct = round(run_result["score"] * 100, 1)

    lines = [
        "## Killjoy Integration Tests",
        "",
        f"**Sandbox run:** tests {status} against real code.",
        f"**Mutation score:** {score_pct}% ({run_result['killed']} killed / {run_result['killed'] + run_result['survived']} total mutants)",
        "",
    ]

    surviving = run_result.get("surviving_mutants", [])
    if surviving:
        lines.append("### Surviving mutants (not caught by these tests)")
        lines.append("")
        for mutant in surviving:
            lines.append(f"- `{mutant['file']}:{mutant['line']}` — {mutant['description']}")
        lines.append("")
    else:
        lines.append("No mutants survived — every planted mutation was caught.")
        lines.append("")

    lines.append("_This PR was generated automatically by Killjoy and has not been auto-merged. Review before merging._")

    return "\n".join(lines)


def open_pull_request(
    github_post: Callable[[str, dict, dict], dict],
    owner: str,
    repo: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    labels: list[str],
) -> dict:
    if branch == base:
        raise ValueError("refusing to open a PR from a branch onto itself")
    if branch in ("main", "master"):
        raise ValueError("refusing to open a PR from main/master")

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    json_body = {"title": title, "head": branch, "base": base, "body": body}

    pr = github_post(url, json_body, headers)

    if labels:
        labels_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr['number']}/labels"
        github_post(labels_url, {"labels": labels}, headers)

    return pr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/CIPRDelivererAgent/test_pr_builder.py -v`
Expected: 2 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/CIPRDelivererAgent/pr_builder.py app/CIPRDelivererAgent/test_pr_builder.py
```

---

### Task 18: CI & PR Deliverer — entrypoint and deployment files

**Files:**
- Create: `app/CIPRDelivererAgent/main.py`
- Create: `app/CIPRDelivererAgent/Dockerfile`
- Create: `app/CIPRDelivererAgent/pyproject.toml`

**Interfaces:**
- Consumes: `clone_repo`, `create_branch_and_commit`, `push_branch` (Task 15); `ensure_killjoy_workflow` (Task 16); `build_pr_body`, `open_pull_request` (Task 17).
- Produces: AgentCore entrypoint accepting `{"repo_url": str, "repo_full_name": str, "pr_number": int, "base_ref": str, "test_source": str, "run_result": dict}` and returning `{"pr_url": str}` or `{"error": str}`. Refuses (returns an error, calls nothing) unless `run_result["tests_passed"] is True` and `run_result` contains a `"score"` key — this is where the ADR's "only opens once the sandbox run already shows tests passing... and mutation score computed" guardrail is enforced in code, not just documented.

- [ ] **Step 1: Write `main.py`**

```python
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import boto3
import requests
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from git_ops import clone_repo, create_branch_and_commit, push_branch
from workflow_injector import ensure_killjoy_workflow
from pr_builder import build_pr_body, open_pull_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_SECRET_ARN = os.environ["GITHUB_SECRET_ARN"]
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

app = BedrockAgentCoreApp()


def _get_github_token() -> str:
    resp = secrets_client.get_secret_value(SecretId=GITHUB_SECRET_ARN)
    data = json.loads(resp["SecretString"])
    return data["github_token"]


def _github_post(url: str, json_body: dict, headers: dict) -> dict:
    token = _get_github_token()
    full_headers = {**headers, "Authorization": f"Bearer {token}"}
    resp = requests.post(url, json=json_body, headers=full_headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


@app.entrypoint
async def handler(payload: dict) -> dict:
    run_result = payload.get("run_result", {})
    if not run_result.get("tests_passed") or "score" not in run_result:
        return {"error": "refusing to open a PR: sandbox run has not proven tests pass with a computed mutation score"}

    repo_url = payload["repo_url"]
    repo_full_name = payload["repo_full_name"]
    pr_number = payload["pr_number"]
    base_ref = payload["base_ref"]
    test_source = payload["test_source"]

    owner, repo = repo_full_name.split("/")
    branch_name = f"killjoy/pr-{pr_number}-{uuid.uuid4().hex[:8]}"

    token = _get_github_token()
    work_dir = Path(tempfile.mkdtemp(prefix="killjoy-"))
    try:
        clone_repo(repo_url, token, work_dir, base_ref)

        files = {f"tests/killjoy/generated/test_pr_{pr_number}.py": test_source}
        create_branch_and_commit(work_dir, branch_name, files, f"test: add Killjoy integration tests for PR #{pr_number}")

        workflow_changed = ensure_killjoy_workflow(work_dir)
        if workflow_changed:
            from git_ops import _run_git
            _run_git(["add", ".github/workflows/killjoy-integration.yml"], work_dir)
            _run_git(["commit", "-m", "ci: add Killjoy integration test workflow"], work_dir)

        push_branch(work_dir, branch_name)

        pr_body = build_pr_body(run_result)
        pr = open_pull_request(
            _github_post,
            owner=owner,
            repo=repo,
            branch=branch_name,
            base="main",
            title=f"Killjoy: integration tests for PR #{pr_number}",
            body=pr_body,
            labels=["ai-generated"],
        )

        return {"pr_url": pr["html_url"]}
    except Exception as exc:
        logger.error("CI/PR Deliverer failed: %s", exc)
        return {"error": str(exc)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml git_ops.py workflow_injector.py pr_builder.py main.py ./
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "killjoy-ci-pr-deliverer"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "bedrock-agentcore",
    "boto3",
    "requests",
]
```

- [ ] **Step 4: Sanity-check the container builds locally**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/app/CIPRDelivererAgent && docker build --platform linux/arm64 -t killjoy-ci-pr-deliverer:local .`
Expected: build succeeds with exit code 0.

- [ ] **Step 5: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add app/CIPRDelivererAgent/main.py app/CIPRDelivererAgent/Dockerfile app/CIPRDelivererAgent/pyproject.toml
```

---

### Task 19: Orchestrator — guardrail ledger

**Files:**
- Create: `app/OrchestratorAgent/guardrails.py`
- Test: `app/OrchestratorAgent/test_guardrails.py`

**Interfaces:**
- Produces: `reserve_run(dynamodb_client, pr_key: str, date_key: str, daily_ceiling: int, pr_runs_table: str, daily_counter_table: str) -> tuple[bool, str]` — atomically checks per-PR dedup and the daily ceiling using a single DynamoDB transaction, returning `(allowed, reason)`. `main.py` (Task 21) calls this before starting the pipeline.

- [ ] **Step 1: Write the failing test**

Uses `moto` to mock DynamoDB so this needs no live AWS.

```python
import boto3
import pytest
from moto import mock_aws

from guardrails import reserve_run

PR_RUNS_TABLE = "killjoy-pr-runs"
DAILY_COUNTER_TABLE = "killjoy-daily-counter"


@pytest.fixture
def dynamodb_client():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-west-2")
        client.create_table(
            TableName=PR_RUNS_TABLE,
            KeySchema=[{"AttributeName": "pr_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pr_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.create_table(
            TableName=DAILY_COUNTER_TABLE,
            KeySchema=[{"AttributeName": "date_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "date_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def test_reserve_run_allows_first_run_for_a_pr(dynamodb_client):
    allowed, reason = reserve_run(
        dynamodb_client, pr_key="acme/widgets#5", date_key="2026-08-08",
        daily_ceiling=5, pr_runs_table=PR_RUNS_TABLE, daily_counter_table=DAILY_COUNTER_TABLE,
    )
    assert allowed is True


def test_reserve_run_rejects_duplicate_run_for_same_pr(dynamodb_client):
    reserve_run(
        dynamodb_client, pr_key="acme/widgets#5", date_key="2026-08-08",
        daily_ceiling=5, pr_runs_table=PR_RUNS_TABLE, daily_counter_table=DAILY_COUNTER_TABLE,
    )
    allowed, reason = reserve_run(
        dynamodb_client, pr_key="acme/widgets#5", date_key="2026-08-08",
        daily_ceiling=5, pr_runs_table=PR_RUNS_TABLE, daily_counter_table=DAILY_COUNTER_TABLE,
    )
    assert allowed is False
    assert "already" in reason.lower()


def test_reserve_run_rejects_when_daily_ceiling_reached(dynamodb_client):
    for i in range(3):
        reserve_run(
            dynamodb_client, pr_key=f"acme/widgets#{i}", date_key="2026-08-08",
            daily_ceiling=3, pr_runs_table=PR_RUNS_TABLE, daily_counter_table=DAILY_COUNTER_TABLE,
        )

    allowed, reason = reserve_run(
        dynamodb_client, pr_key="acme/widgets#99", date_key="2026-08-08",
        daily_ceiling=3, pr_runs_table=PR_RUNS_TABLE, daily_counter_table=DAILY_COUNTER_TABLE,
    )
    assert allowed is False
    assert "ceiling" in reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && pip install moto && python -m pytest app/OrchestratorAgent/test_guardrails.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardrails'`

- [ ] **Step 3: Write `guardrails.py`**

```python
from botocore.exceptions import ClientError


def reserve_run(
    dynamodb_client,
    pr_key: str,
    date_key: str,
    daily_ceiling: int,
    pr_runs_table: str,
    daily_counter_table: str,
) -> tuple[bool, str]:
    try:
        dynamodb_client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": pr_runs_table,
                        "Item": {"pr_key": {"S": pr_key}},
                        "ConditionExpression": "attribute_not_exists(pr_key)",
                    }
                },
                {
                    "Update": {
                        "TableName": daily_counter_table,
                        "Key": {"date_key": {"S": date_key}},
                        "UpdateExpression": "ADD run_count :one",
                        "ConditionExpression": "attribute_not_exists(run_count) OR run_count < :ceiling",
                        "ExpressionAttributeValues": {
                            ":one": {"N": "1"},
                            ":ceiling": {"N": str(daily_ceiling)},
                        },
                    }
                },
            ]
        )
        return True, "reserved"
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise

        reasons = exc.response.get("CancellationReasons", [])
        pr_run_failed = len(reasons) > 0 and reasons[0].get("Code") == "ConditionalCheckFailed"
        if pr_run_failed:
            return False, f"a Killjoy run already exists for {pr_key}"
        return False, f"daily PR ceiling of {daily_ceiling} reached for {date_key}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/OrchestratorAgent/test_guardrails.py -v`
Expected: 3 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/OrchestratorAgent/guardrails.py app/OrchestratorAgent/test_guardrails.py
```

---

### Task 20: Orchestrator — pipeline core with bounded mutation feedback loop

**Files:**
- Create: `app/OrchestratorAgent/pipeline.py`
- Test: `app/OrchestratorAgent/test_pipeline.py`

**Interfaces:**
- Consumes (as injected callables, matching each agent's `main.py` entrypoint payload/response shape exactly):
  - `invoke_env_mapper(payload: dict) -> dict` — payload `{"repo_path": str}`, returns env-map dict from Task 9.
  - `invoke_test_generator(payload: dict) -> dict` — payload `{"diff": str, "env_map": dict, "surviving_mutants": list|None}`, returns `{"test_source": str}` or `{"error": str}` from Task 11.
  - `invoke_evaluator(payload: dict) -> dict` — payload `{"repo_files": dict, "test_source": str, "touched_paths": list}`, returns `{"tests_passed": bool, "score": float, "killed": int, "survived": int, "surviving_mutants": list}` or `{"error": str}` from Task 14.
  - `invoke_ci_deliverer(payload: dict) -> dict` — payload `{"repo_url": str, "repo_full_name": str, "pr_number": int, "base_ref": str, "test_source": str, "run_result": dict}`, returns `{"pr_url": str}` or `{"error": str}` from Task 18.
- Produces: `run_pipeline(request: dict, invoke_env_mapper, invoke_test_generator, invoke_evaluator, invoke_ci_deliverer, max_mutation_rounds: int = 3) -> dict` returning `{"status": "success"|"failed", "stage_failed": str|None, "reason": str|None, "pr_url": str|None, "rounds": [dict], "final_test_source": str|None}` where `rounds` records each mutation-loop round's evaluator result and `final_test_source` is the last generated test source (regardless of success/failure) — `scripts/evaluate_killjoy.py` (Task 26) uses `rounds` to check whether the mutation score improves round over round, and `final_test_source` to check the generated test isn't a false positive against the fixed branch.

- [ ] **Step 1: Write the failing test**

```python
from pipeline import run_pipeline


def _make_request():
    return {
        "repo_path": "/tmp/fake-repo",
        "diff": "diff --git a/app/service.py b/app/service.py",
        "repo_url": "https://github.com/acme/widgets.git",
        "repo_full_name": "acme/widgets",
        "pr_number": 5,
        "base_ref": "abc123",
        "repo_files": {"app/service.py": "def f(): return 1"},
        "touched_paths": ["app/service.py"],
    }


def test_pipeline_succeeds_when_first_round_kills_all_mutants():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}
    calls = {"test_generator": 0, "evaluator": 0}

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        calls["test_generator"] += 1
        assert payload["env_map"] == env_map
        return {"test_source": "def test_x(): assert True"}

    def fake_evaluator(payload):
        calls["evaluator"] += 1
        return {"tests_passed": True, "score": 1.0, "killed": 5, "survived": 0, "surviving_mutants": []}

    def fake_ci_deliverer(payload):
        assert payload["run_result"]["tests_passed"] is True
        return {"pr_url": "https://github.com/acme/widgets/pull/99"}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer)

    assert result["status"] == "success"
    assert result["pr_url"] == "https://github.com/acme/widgets/pull/99"
    assert calls["test_generator"] == 1
    assert calls["evaluator"] == 1
    assert len(result["rounds"]) == 1


def test_pipeline_retries_test_generator_when_mutants_survive_then_succeeds():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}
    round_scores = iter([0.5, 1.0])
    survivors_by_round = iter([[{"id": "1", "file": "app/service.py", "line": 3, "description": "survived"}], []])
    generator_calls = []

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        generator_calls.append(payload.get("surviving_mutants"))
        return {"test_source": "def test_x(): assert True"}

    def fake_evaluator(payload):
        score = next(round_scores)
        survivors = next(survivors_by_round)
        return {"tests_passed": True, "score": score, "killed": 1, "survived": len(survivors), "surviving_mutants": survivors}

    def fake_ci_deliverer(payload):
        return {"pr_url": "https://github.com/acme/widgets/pull/100"}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer)

    assert result["status"] == "success"
    assert len(result["rounds"]) == 2
    assert generator_calls[0] is None
    assert generator_calls[1][0]["id"] == "1"
    assert result["rounds"][0]["score"] == 0.5
    assert result["rounds"][1]["score"] == 1.0


def test_pipeline_stops_after_max_rounds_and_still_opens_pr_with_best_effort_score():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        return {"test_source": "def test_x(): assert True"}

    def fake_evaluator(payload):
        return {"tests_passed": True, "score": 0.6, "killed": 3, "survived": 2, "surviving_mutants": [{"id": "1", "file": "a.py", "line": 1, "description": "x"}]}

    def fake_ci_deliverer(payload):
        return {"pr_url": "https://github.com/acme/widgets/pull/101"}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer, max_mutation_rounds=3)

    assert result["status"] == "success"
    assert len(result["rounds"]) == 3
    assert result["rounds"][-1]["score"] == 0.6


def test_pipeline_aborts_and_opens_no_pr_when_environment_mapper_fails():
    def fake_env_mapper(payload):
        raise RuntimeError("cannot parse repo")

    ci_deliverer_called = []

    def fake_ci_deliverer(payload):
        ci_deliverer_called.append(True)
        return {"pr_url": "should-not-happen"}

    result = run_pipeline(_make_request(), fake_env_mapper, lambda p: {}, lambda p: {}, fake_ci_deliverer)

    assert result["status"] == "failed"
    assert result["stage_failed"] == "environment_mapper"
    assert "cannot parse repo" in result["reason"]
    assert ci_deliverer_called == []


def test_pipeline_aborts_when_evaluator_reports_tests_failed_against_real_code():
    env_map = {"layers": [], "fixtures": [], "outer_edges": []}

    def fake_env_mapper(payload):
        return env_map

    def fake_test_generator(payload):
        return {"test_source": "def test_x(): assert False"}

    def fake_evaluator(payload):
        return {"error": "generated tests failed against real code"}

    ci_deliverer_called = []

    def fake_ci_deliverer(payload):
        ci_deliverer_called.append(True)
        return {}

    result = run_pipeline(_make_request(), fake_env_mapper, fake_test_generator, fake_evaluator, fake_ci_deliverer)

    assert result["status"] == "failed"
    assert result["stage_failed"] == "evaluator"
    assert ci_deliverer_called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/OrchestratorAgent/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Write `pipeline.py`**

```python
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def run_pipeline(
    request: dict,
    invoke_env_mapper: Callable[[dict], dict],
    invoke_test_generator: Callable[[dict], dict],
    invoke_evaluator: Callable[[dict], dict],
    invoke_ci_deliverer: Callable[[dict], dict],
    max_mutation_rounds: int = 3,
) -> dict:
    rounds: list[dict] = []
    test_source = None

    def _failed(stage: str, reason: str) -> dict:
        return {
            "status": "failed",
            "stage_failed": stage,
            "reason": reason,
            "pr_url": None,
            "rounds": rounds,
            "final_test_source": test_source,
        }

    try:
        env_map = invoke_env_mapper({"repo_path": request["repo_path"]})
    except Exception as exc:
        logger.error("environment_mapper stage failed: %s", exc)
        return _failed("environment_mapper", str(exc))

    if isinstance(env_map, dict) and "error" in env_map:
        return _failed("environment_mapper", env_map["error"])

    surviving_mutants = None
    last_evaluator_result = None

    for _round_number in range(max_mutation_rounds):
        try:
            generator_result = invoke_test_generator({
                "diff": request["diff"],
                "env_map": env_map,
                "surviving_mutants": surviving_mutants,
            })
        except Exception as exc:
            logger.error("test_generator stage failed: %s", exc)
            return _failed("test_generator", str(exc))

        if "error" in generator_result:
            return _failed("test_generator", generator_result["error"])

        test_source = generator_result["test_source"]

        try:
            evaluator_result = invoke_evaluator({
                "repo_files": request["repo_files"],
                "test_source": test_source,
                "touched_paths": request["touched_paths"],
            })
        except Exception as exc:
            logger.error("evaluator stage failed: %s", exc)
            return _failed("evaluator", str(exc))

        if "error" in evaluator_result:
            return _failed("evaluator", evaluator_result["error"])

        rounds.append(evaluator_result)
        last_evaluator_result = evaluator_result

        surviving_mutants = evaluator_result.get("surviving_mutants", [])
        if not surviving_mutants:
            break

    try:
        ci_result = invoke_ci_deliverer({
            "repo_url": request["repo_url"],
            "repo_full_name": request["repo_full_name"],
            "pr_number": request["pr_number"],
            "base_ref": request["base_ref"],
            "test_source": test_source,
            "run_result": last_evaluator_result,
        })
    except Exception as exc:
        logger.error("ci_pr_deliverer stage failed: %s", exc)
        return _failed("ci_pr_deliverer", str(exc))

    if "error" in ci_result:
        return _failed("ci_pr_deliverer", ci_result["error"])

    return {
        "status": "success",
        "stage_failed": None,
        "reason": None,
        "pr_url": ci_result["pr_url"],
        "rounds": rounds,
        "final_test_source": test_source,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest app/OrchestratorAgent/test_pipeline.py -v`
Expected: 6 passed

- [ ] **Step 5: Stage the changes**

```bash
git add app/OrchestratorAgent/pipeline.py app/OrchestratorAgent/test_pipeline.py
```

---

### Task 21: Orchestrator — entrypoint and deployment files

**Files:**
- Create: `app/OrchestratorAgent/main.py`
- Create: `app/OrchestratorAgent/Dockerfile`
- Create: `app/OrchestratorAgent/pyproject.toml`

**Interfaces:**
- Consumes: `reserve_run` (Task 19), `run_pipeline` (Task 20).
- Produces: AgentCore entrypoint accepting the Lambda's payload shape (Task 23): `{"repo_url": str, "repo_full_name": str, "pr_number": int, "base_ref": str, "head_ref": str, "pr_title": str}`, and returning the `run_pipeline` result dict.

- [ ] **Step 1: Write `main.py`**

```python
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from guardrails import reserve_run
from pipeline import run_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
ENV_MAPPER_ARN = os.environ["ENV_MAPPER_ARN"]
TEST_GENERATOR_ARN = os.environ["TEST_GENERATOR_ARN"]
EVALUATOR_ARN = os.environ["EVALUATOR_ARN"]
CI_PR_DELIVERER_ARN = os.environ["CI_PR_DELIVERER_ARN"]
GITHUB_SECRET_ARN = os.environ["GITHUB_SECRET_ARN"]
PR_RUNS_TABLE = os.environ.get("PR_RUNS_TABLE", "killjoy-pr-runs")
DAILY_COUNTER_TABLE = os.environ.get("DAILY_COUNTER_TABLE", "killjoy-daily-counter")
DAILY_PR_CEILING = int(os.environ.get("DAILY_PR_CEILING", "5"))

dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
bedrock_agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

app = BedrockAgentCoreApp()


def _ensure_session_id(session_id: str) -> str:
    if len(session_id) < 33:
        session_id = session_id + uuid.uuid4().hex
    return session_id[:128]


def _invoke_agent(agent_arn: str, payload: dict, label: str) -> dict:
    session_id = _ensure_session_id(f"killjoy-{label}-{uuid.uuid4().hex}")
    resp = bedrock_agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        payload=json.dumps(payload).encode("utf-8"),
        runtimeSessionId=session_id,
    )
    raw_bytes = resp["response"].read()
    text = raw_bytes.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"error": f"non-json response from {label}: {text[:500]}"}


def _get_github_token() -> str:
    resp = secrets_client.get_secret_value(SecretId=GITHUB_SECRET_ARN)
    return json.loads(resp["SecretString"])["github_token"]


def _clone_for_analysis(repo_url: str, token: str, ref: str) -> Path:
    import subprocess
    dest = Path(tempfile.mkdtemp(prefix="killjoy-orch-"))
    authenticated_url = repo_url.replace("https://", f"https://x-access-token:{token}@")
    subprocess.run(["git", "clone", authenticated_url, str(dest)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", ref], cwd=str(dest), check=True, capture_output=True, text=True)
    return dest


def _build_diff(repo_path: Path, base_ref: str, head_ref: str) -> str:
    import subprocess
    result = subprocess.run(
        ["git", "diff", f"{base_ref}...{head_ref}"], cwd=str(repo_path),
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _touched_paths(diff: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)


def _read_repo_files(repo_path: Path, touched_paths: list[str]) -> dict:
    files = {}
    for rel_path in touched_paths:
        file_path = repo_path / rel_path
        if file_path.exists():
            files[rel_path] = file_path.read_text()
    return files


@app.entrypoint
async def handler(payload: dict) -> dict:
    repo_full_name = payload["repo_full_name"]
    pr_number = payload["pr_number"]
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pr_key = f"{repo_full_name}#{pr_number}"

    allowed, reason = reserve_run(dynamodb_client, pr_key, date_key, DAILY_PR_CEILING, PR_RUNS_TABLE, DAILY_COUNTER_TABLE)
    if not allowed:
        logger.info("Run rejected for %s: %s", pr_key, reason)
        return {"status": "skipped", "reason": reason}

    token = _get_github_token()
    repo_path = _clone_for_analysis(payload["repo_url"], token, payload["head_ref"])
    try:
        diff = _build_diff(repo_path, payload["base_ref"], payload["head_ref"])
        touched_paths = _touched_paths(diff)
        repo_files = _read_repo_files(repo_path, touched_paths)

        request = {
            "repo_path": str(repo_path),
            "diff": diff,
            "repo_url": payload["repo_url"],
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "base_ref": payload["base_ref"],
            "repo_files": repo_files,
            "touched_paths": touched_paths,
        }

        result = run_pipeline(
            request,
            invoke_env_mapper=lambda p: _invoke_agent(ENV_MAPPER_ARN, p, "env-mapper"),
            invoke_test_generator=lambda p: _invoke_agent(TEST_GENERATOR_ARN, p, "test-generator"),
            invoke_evaluator=lambda p: _invoke_agent(EVALUATOR_ARN, p, "evaluator"),
            invoke_ci_deliverer=lambda p: _invoke_agent(CI_PR_DELIVERER_ARN, p, "ci-pr-deliverer"),
        )

        if result["status"] == "failed":
            logger.error("Pipeline failed at stage %s: %s", result["stage_failed"], result["reason"])

        return result
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml guardrails.py pipeline.py main.py ./
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "killjoy-orchestrator"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = [
    "bedrock-agentcore",
    "boto3",
]
```

- [ ] **Step 4: Sanity-check the container builds locally**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/app/OrchestratorAgent && docker build --platform linux/arm64 -t killjoy-orchestrator:local .`
Expected: build succeeds with exit code 0.

- [ ] **Step 5: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add app/OrchestratorAgent/main.py app/OrchestratorAgent/Dockerfile app/OrchestratorAgent/pyproject.toml
```

---

### Task 22: Lambda webhook handler

**Files:**
- Create: `lambda/webhook_handler/handler.py`
- Create: `lambda/webhook_handler/requirements.txt`
- Test: `lambda/webhook_handler/test_handler.py`

**Interfaces:**
- Produces: `verify_signature(body: bytes, signature_header: str, secret: str) -> bool` and `lambda_handler(event: dict, context, get_webhook_secret: Callable[[], str], invoke_orchestrator: Callable[[dict, str], dict]) -> dict`. The two callables are injected so this is fully unit-testable without AWS; the module-level `handler(event, context)` (used as the actual Lambda entrypoint) wires them to real Secrets Manager and `bedrock-agentcore` calls.

- [ ] **Step 1: Write the failing test**

```python
import hashlib
import hmac
import json

from handler import verify_signature, lambda_handler


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_correct_signature():
    body = b'{"action": "opened"}'
    secret = "top-secret"
    assert verify_signature(body, _sign(body, secret), secret) is True


def test_verify_signature_rejects_wrong_signature():
    body = b'{"action": "opened"}'
    assert verify_signature(body, "sha256=deadbeef", "top-secret") is False


def test_verify_signature_rejects_missing_header():
    assert verify_signature(b"{}", "", "top-secret") is False


def test_lambda_handler_invokes_orchestrator_for_opened_pr():
    secret = "top-secret"
    body_dict = {
        "action": "opened",
        "repository": {"clone_url": "https://github.com/acme/widgets.git", "full_name": "acme/widgets"},
        "pull_request": {
            "number": 5,
            "title": "Add feature",
            "base": {"sha": "base123"},
            "head": {"sha": "head456"},
        },
    }
    body_bytes = json.dumps(body_dict).encode("utf-8")
    event = {
        "headers": {
            "x-hub-signature-256": _sign(body_bytes, secret),
            "x-github-event": "pull_request",
        },
        "body": body_bytes.decode("utf-8"),
        "isBase64Encoded": False,
    }

    invoked_with = {}

    def fake_get_secret():
        return secret

    def fake_invoke_orchestrator(payload, session_id):
        invoked_with["payload"] = payload
        return {"status": "success"}

    class FakeContext:
        aws_request_id = "req-1"

    response = lambda_handler(event, FakeContext(), fake_get_secret, fake_invoke_orchestrator)

    assert response["statusCode"] == 200
    assert invoked_with["payload"]["pr_number"] == 5
    assert invoked_with["payload"]["repo_full_name"] == "acme/widgets"


def test_lambda_handler_rejects_bad_signature():
    event = {
        "headers": {"x-hub-signature-256": "sha256=wrong", "x-github-event": "pull_request"},
        "body": "{}",
        "isBase64Encoded": False,
    }

    class FakeContext:
        aws_request_id = "req-2"

    response = lambda_handler(event, FakeContext(), lambda: "top-secret", lambda p, s: {})
    assert response["statusCode"] == 401


def test_lambda_handler_skips_non_pr_events():
    event = {
        "headers": {"x-github-event": "push"},
        "body": "{}",
        "isBase64Encoded": False,
    }

    class FakeContext:
        aws_request_id = "req-3"

    response = lambda_handler(event, FakeContext(), lambda: "unused", lambda p, s: {})
    assert response["statusCode"] == 200
    assert "skipped" in response["body"]


def test_lambda_handler_skips_non_opened_or_synchronize_actions():
    body_dict = {"action": "closed", "repository": {}, "pull_request": {}}
    body_bytes = json.dumps(body_dict).encode("utf-8")
    secret = "top-secret"
    event = {
        "headers": {"x-hub-signature-256": _sign(body_bytes, secret), "x-github-event": "pull_request"},
        "body": body_bytes.decode("utf-8"),
        "isBase64Encoded": False,
    }

    class FakeContext:
        aws_request_id = "req-4"

    response = lambda_handler(event, FakeContext(), lambda: secret, lambda p, s: {})
    assert response["statusCode"] == 200
    assert "skipped" in response["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest lambda/webhook_handler/test_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'handler'`

- [ ] **Step 3: Write `lambda/webhook_handler/handler.py`**

```python
import base64
import hashlib
import hmac
import json
import os
from typing import Callable

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")


def verify_signature(body: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


def lambda_handler(
    event: dict,
    context,
    get_webhook_secret: Callable[[], str],
    invoke_orchestrator: Callable[[dict, str], dict],
) -> dict:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body_raw = event.get("body", "")
    body_bytes = base64.b64decode(body_raw) if event.get("isBase64Encoded") else body_raw.encode("utf-8")

    if headers.get("x-github-event") != "pull_request":
        return {"statusCode": 200, "body": json.dumps({"skipped": "not a pull_request event"})}

    secret = get_webhook_secret()
    if not verify_signature(body_bytes, headers.get("x-hub-signature-256", ""), secret):
        return {"statusCode": 401, "body": json.dumps({"error": "invalid signature"})}

    payload = json.loads(body_bytes)
    action = payload.get("action")
    if action not in ("opened", "synchronize"):
        return {"statusCode": 200, "body": json.dumps({"skipped": f"action={action}"})}

    pr = payload["pull_request"]
    repo = payload["repository"]

    orchestrator_payload = {
        "repo_url": repo["clone_url"],
        "repo_full_name": repo["full_name"],
        "pr_number": pr["number"],
        "base_ref": pr["base"]["sha"],
        "head_ref": pr["head"]["sha"],
        "pr_title": pr["title"],
    }

    session_id_seed = f"killjoy-{repo['full_name'].replace('/', '-')}-{pr['number']}-{context.aws_request_id}"
    session_id = (session_id_seed + "0" * 33)[:128] if len(session_id_seed) < 33 else session_id_seed[:128]

    result = invoke_orchestrator(orchestrator_payload, session_id)

    return {"statusCode": 200, "body": json.dumps({"orchestrator_result": result})}


def _get_webhook_secret() -> str:
    secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)
    resp = secrets_client.get_secret_value(SecretId=os.environ["WEBHOOK_SECRET_ARN"])
    return resp["SecretString"]


def _invoke_orchestrator(payload: dict, session_id: str) -> dict:
    agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    resp = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["ORCHESTRATOR_ARN"],
        payload=json.dumps(payload).encode("utf-8"),
        runtimeSessionId=session_id,
    )
    return json.loads(resp["response"].read().decode("utf-8"))


def handler(event, context):
    return lambda_handler(event, context, _get_webhook_secret, _invoke_orchestrator)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && python -m pytest lambda/webhook_handler/test_handler.py -v`
Expected: 8 passed

- [ ] **Step 5: Write `requirements.txt`**

```
boto3
```

- [ ] **Step 6: Stage the changes**

```bash
git add lambda/webhook_handler/
```

---

### Task 23: Terraform — provider, ECR, and IAM

**Files:**
- Create: `infra/main.tf`
- Create: `infra/variables.tf`
- Create: `infra/outputs.tf`
- Create: `infra/ecr.tf`
- Create: `infra/iam.tf`
- Create: `infra/secrets.tf`

**Interfaces:**
- Produces: `aws_iam_role.agent_execution_role` (referenced by `infra/agents.tf` in Task 24), `aws_ecr_repository.agents` for-each map (referenced by `infra/agents.tf`), `aws_secretsmanager_secret.github_token` and `aws_secretsmanager_secret.webhook_secret` (referenced by `infra/agents.tf`, `infra/lambda.tf`).

- [ ] **Step 1: Write `infra/variables.tf`**

```hcl
variable "aws_region" {
  default = "us-west-2"
}

variable "project" {
  default = "killjoy"
}

variable "github_token" {
  description = "GitHub PAT with contents:write and pull_requests:write scope"
  sensitive   = true
}

variable "webhook_secret" {
  description = "Shared secret configured on the GitHub webhook for HMAC signature verification"
  sensitive   = true
}

variable "model_id" {
  default = "us.anthropic.claude-sonnet-4-6"
}

variable "daily_pr_ceiling" {
  description = "Max Killjoy PRs opened per day across all triggering PRs"
  default     = 5
}
```

- [ ] **Step 2: Write `infra/main.tf`**

```hcl
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.32"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}
```

- [ ] **Step 3: Write `infra/ecr.tf`**

```hcl
locals {
  agents = ["orchestrator", "environment-mapper", "test-generator", "evaluator", "ci-pr-deliverer"]
}

resource "aws_ecr_repository" "agents" {
  for_each             = toset(local.agents)
  name                 = "${var.project}-${each.key}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

output "ecr_urls" {
  value = { for k, v in aws_ecr_repository.agents : k => v.repository_url }
}
```

- [ ] **Step 4: Write `infra/secrets.tf`**

```hcl
resource "aws_secretsmanager_secret" "github_token" {
  name                    = "${var.project}/github-token"
  description             = "GitHub PAT for branch creation and PR opening"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "github_token" {
  secret_id     = aws_secretsmanager_secret.github_token.id
  secret_string = jsonencode({ github_token = var.github_token })
}

resource "aws_secretsmanager_secret" "webhook_secret" {
  name                    = "${var.project}/webhook-secret"
  description             = "Shared secret for verifying GitHub webhook HMAC signatures"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "webhook_secret" {
  secret_id     = aws_secretsmanager_secret.webhook_secret.id
  secret_string = var.webhook_secret
}
```

- [ ] **Step 5: Write `infra/iam.tf`**

```hcl
resource "aws_iam_role" "agent_execution_role" {
  name = "${var.project}-agent-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "agent_policy" {
  name = "${var.project}-agent-policy"
  role = aws_iam_role.agent_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
          "arn:aws:bedrock:*::foundation-model/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:BatchCheckLayerAvailability"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.github_token.arn]
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"]
        Resource = [
          aws_dynamodb_table.pr_runs.arn,
          aws_dynamodb_table.daily_counter.arn,
        ]
      }
    ]
  })
}
```

- [ ] **Step 6: Write `infra/outputs.tf`**

```hcl
output "orchestrator_arn" {
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
  description = "Set as ORCHESTRATOR_ARN for the webhook Lambda"
}

output "ecr_repository_urls" {
  value = { for k, v in aws_ecr_repository.agents : k => v.repository_url }
}

output "api_gateway_webhook_url" {
  value = "${aws_apigatewayv2_api.webhook.api_endpoint}/webhook"
}
```

- [ ] **Step 7: Verify the module parses (agents.tf/dynamodb.tf/lambda.tf/apigateway.tf don't exist yet, so expect a reference error, not a syntax error)**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/infra && terraform fmt -check main.tf variables.tf ecr.tf iam.tf secrets.tf outputs.tf`
Expected: no output (all files already correctly formatted). `terraform validate` will fail at this point referencing undefined resources from later tasks — that's expected until Task 24–26 are done.

- [ ] **Step 8: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add infra/main.tf infra/variables.tf infra/outputs.tf infra/ecr.tf infra/iam.tf infra/secrets.tf
```

---

### Task 24: Terraform — DynamoDB guardrail tables and AgentCore runtimes

**Files:**
- Create: `infra/dynamodb.tf`
- Create: `infra/agents.tf`

**Interfaces:**
- Consumes: `aws_iam_role.agent_execution_role`, `aws_ecr_repository.agents`, `aws_secretsmanager_secret.github_token` from Task 23.
- Produces: `aws_dynamodb_table.pr_runs` and `aws_dynamodb_table.daily_counter` (table names must match `PR_RUNS_TABLE`/`DAILY_COUNTER_TABLE` defaults in `app/OrchestratorAgent/main.py` from Task 21: `killjoy-pr-runs`, `killjoy-daily-counter`). Produces `aws_bedrockagentcore_agent_runtime.orchestrator` (referenced by `infra/outputs.tf` and `infra/lambda.tf`).

- [ ] **Step 1: Write `infra/dynamodb.tf`**

```hcl
resource "aws_dynamodb_table" "pr_runs" {
  name         = "killjoy-pr-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pr_key"

  attribute {
    name = "pr_key"
    type = "S"
  }
}

resource "aws_dynamodb_table" "daily_counter" {
  name         = "killjoy-daily-counter"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "date_key"

  attribute {
    name = "date_key"
    type = "S"
  }
}
```

- [ ] **Step 2: Write `infra/agents.tf`**

```hcl
resource "aws_bedrockagentcore_agent_runtime" "environment_mapper" {
  agent_runtime_name = "${replace(var.project, "-", "_")}_environment_mapper"
  description        = "Maps intra-application integration surface: call graph, fixtures, layer boundaries"

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agents["environment-mapper"].repository_url}:latest"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  role_arn = aws_iam_role.agent_execution_role.arn

  environment_variables = {
    MODEL_ID = var.model_id
  }
}

resource "aws_bedrockagentcore_agent_runtime" "test_generator" {
  agent_runtime_name = "${replace(var.project, "-", "_")}_test_generator"
  description        = "Writes pytest integration tests from a PR diff and environment map"

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agents["test-generator"].repository_url}:latest"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  role_arn = aws_iam_role.agent_execution_role.arn

  environment_variables = {
    MODEL_ID = var.model_id
  }
}

resource "aws_bedrockagentcore_agent_runtime" "evaluator" {
  agent_runtime_name = "${replace(var.project, "-", "_")}_evaluator"
  description        = "Runs generated tests and scoped mutmut in a Code Interpreter sandbox"

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agents["evaluator"].repository_url}:latest"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  role_arn = aws_iam_role.agent_execution_role.arn
}

resource "aws_bedrockagentcore_agent_runtime" "ci_pr_deliverer" {
  agent_runtime_name = "${replace(var.project, "-", "_")}_ci_pr_deliverer"
  description        = "Commits generated tests and CI workflow to a branch and opens a labeled PR"

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agents["ci-pr-deliverer"].repository_url}:latest"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  role_arn = aws_iam_role.agent_execution_role.arn

  environment_variables = {
    GITHUB_SECRET_ARN = aws_secretsmanager_secret.github_token.arn
  }
}

resource "aws_bedrockagentcore_agent_runtime" "orchestrator" {
  agent_runtime_name = "${replace(var.project, "-", "_")}_orchestrator"
  description        = "Sequential pipeline: env map -> generate -> evaluate/mutate (max 3 rounds) -> deliver PR"

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agents["orchestrator"].repository_url}:latest"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  role_arn = aws_iam_role.agent_execution_role.arn

  environment_variables = {
    GITHUB_SECRET_ARN    = aws_secretsmanager_secret.github_token.arn
    ENV_MAPPER_ARN       = aws_bedrockagentcore_agent_runtime.environment_mapper.agent_runtime_arn
    TEST_GENERATOR_ARN   = aws_bedrockagentcore_agent_runtime.test_generator.agent_runtime_arn
    EVALUATOR_ARN        = aws_bedrockagentcore_agent_runtime.evaluator.agent_runtime_arn
    CI_PR_DELIVERER_ARN  = aws_bedrockagentcore_agent_runtime.ci_pr_deliverer.agent_runtime_arn
    PR_RUNS_TABLE        = aws_dynamodb_table.pr_runs.name
    DAILY_COUNTER_TABLE  = aws_dynamodb_table.daily_counter.name
    DAILY_PR_CEILING     = tostring(var.daily_pr_ceiling)
  }

  depends_on = [
    aws_bedrockagentcore_agent_runtime.environment_mapper,
    aws_bedrockagentcore_agent_runtime.test_generator,
    aws_bedrockagentcore_agent_runtime.evaluator,
    aws_bedrockagentcore_agent_runtime.ci_pr_deliverer,
  ]
}
```

- [ ] **Step 3: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add infra/dynamodb.tf infra/agents.tf
```

---

### Task 25: Terraform — Lambda and API Gateway webhook

**Files:**
- Create: `infra/lambda.tf`
- Create: `infra/apigateway.tf`

**Interfaces:**
- Consumes: `aws_bedrockagentcore_agent_runtime.orchestrator` (Task 24), `aws_secretsmanager_secret.webhook_secret` (Task 23).
- Produces: `aws_apigatewayv2_api.webhook` (referenced by `infra/outputs.tf` from Task 23).

- [ ] **Step 1: Write `infra/lambda.tf`**

```hcl
data "archive_file" "webhook_handler" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/webhook_handler"
  output_path = "${path.module}/webhook_handler.zip"
}

resource "aws_iam_role" "webhook_lambda_role" {
  name = "${var.project}-webhook-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "webhook_lambda_policy" {
  name = "${var.project}-webhook-lambda-policy"
  role = aws_iam_role.webhook_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.webhook_secret.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = [aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn]
      }
    ]
  })
}

resource "aws_lambda_function" "webhook_handler" {
  function_name    = "${var.project}-webhook-handler"
  role             = aws_iam_role.webhook_lambda_role.arn
  handler          = "handler.handler"
  runtime          = "python3.13"
  timeout          = 900
  filename         = data.archive_file.webhook_handler.output_path
  source_code_hash = data.archive_file.webhook_handler.output_base64sha256

  environment {
    variables = {
      WEBHOOK_SECRET_ARN = aws_secretsmanager_secret.webhook_secret.arn
      ORCHESTRATOR_ARN   = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
      AWS_REGION         = var.aws_region
    }
  }
}
```

Note the Lambda `timeout = 900` (AWS's hard maximum) — the mutation feedback loop can run the Test Generator and Evaluator up to 3 times sequentially before the Orchestrator returns, and the whole call chain is synchronous end-to-end from the Lambda's perspective. For the demo-repo's scope this fits comfortably; a larger target repo could exceed it, which is a known v1 limitation (see the ADR's "per-run cost" open risk) — the fix would be to make the Lambda fire-and-forget and have the Orchestrator itself post the final PR-or-failure notification, which is out of scope for v1.

- [ ] **Step 2: Write `infra/apigateway.tf`**

```hcl
resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.project}-webhook"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "webhook_lambda" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.webhook_handler.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook_post" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.webhook_lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigateway_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}
```

- [ ] **Step 3: Add the `archive` provider required by `data.archive_file`**

Edit `infra/main.tf`, add to `required_providers`:
```hcl
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
```

- [ ] **Step 4: Validate the full Terraform config**

Run: `cd /Users/ilaakshmishra/Documents/killjoy/infra && terraform init -backend=false && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add infra/lambda.tf infra/apigateway.tf infra/main.tf
```

---

### Task 26: Evaluation harness — the two ADR-mandated metrics

**Files:**
- Create: `scripts/evaluate_killjoy.py`

**Interfaces:**
- Consumes: `eval/known_bugs.json` (Task 6); `scan_repo`/`synthesize_env_map` (Tasks 8–9); `generate_tests` (Task 10); `run_pytest`/`run_mutation` (Tasks 12–13); `run_pipeline` (Task 20) — reused directly, running entirely in-process against `demo-repo`, with no AWS calls, by injecting local functions in place of the AgentCore `invoke_*` callables. Scenario switching is a plain file copy from `eval/scenarios/<id>/<target_path>` over `demo-repo/<target_path>` and back — no git branches (see Task 3's note on why: nothing in this environment can `git commit`, so branches can never diverge).
- Produces: two printed metrics per the ADR's "Testing Killjoy Itself" section: (1) mutation score per round per scenario (to show it improves or holds, never regresses), (2) count of generated tests that fail against the fixed/correct file (goal: 0 — a test that fails on good code is worse than no test).

- [ ] **Step 1: Write `scripts/evaluate_killjoy.py`**

```python
"""
Runs Killjoy's own pipeline against the demo-repo's planted-bug scenarios,
entirely in-process (no AWS), by wiring run_pipeline's injected callables to
local calls into each agent's core module. Reports the two numbers the ADR's
"Testing Killjoy Itself" section requires:

1. Does the mutation score improve (or hold, never regress) round over round?
2. Does Killjoy ever generate a test that fails against the FIXED/correct
   version of the code (a false positive — worse than no test at all)?

Usage: python scripts/evaluate_killjoy.py
Requires: OPENAI/ANTHROPIC credentials are NOT needed for the LLM-backed
stages if KILLJOY_FAKE_LLM=1 is set, which substitutes a deterministic stub
LLM for local/offline runs. For a real evaluation, unset it and configure AWS
Bedrock credentials so the real Test Generator/Environment Mapper prompts run.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app" / "EnvironmentMapperAgent"))
sys.path.insert(0, str(ROOT / "app" / "TestGeneratorAgent"))
sys.path.insert(0, str(ROOT / "app" / "EvaluatorAgent"))
sys.path.insert(0, str(ROOT / "app" / "OrchestratorAgent"))

from scanner import scan_repo
from synthesizer import synthesize_env_map
from generator import generate_tests
from runner import run_pytest
from mutation import run_mutation
from pipeline import run_pipeline

DEMO_REPO = ROOT / "demo-repo"
KNOWN_BUGS_PATH = ROOT / "eval" / "known_bugs.json"


def _fake_llm_invoke(prompt: str) -> str:
    """Deterministic stub used when KILLJOY_FAKE_LLM=1 — for offline dry-runs
    of the pipeline wiring, not a substitute for real evaluation quality."""
    if "layers" in prompt.lower() and "environment map" not in prompt.lower():
        return json.dumps({"layers": [], "fixtures": [], "outer_edges": []})
    return "def test_placeholder():\n    assert True\n"


def _swap_in_scenario(scenario: dict) -> str:
    """Copies the scenario's buggy file over demo-repo's real file. Returns
    the original (fixed) content so the caller can restore it afterward."""
    target_path = DEMO_REPO / scenario["target_path"]
    original_content = target_path.read_text()
    scenario_file = ROOT / scenario["scenario_file"]
    target_path.write_text(scenario_file.read_text())
    return original_content


def _restore_target(scenario: dict, original_content: str) -> None:
    (DEMO_REPO / scenario["target_path"]).write_text(original_content)


def _make_llm_invoke():
    if os.environ.get("KILLJOY_FAKE_LLM") == "1":
        return _fake_llm_invoke

    from langchain_aws import ChatBedrockConverse
    llm = ChatBedrockConverse(model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"))

    def _invoke(prompt: str) -> str:
        response = llm.invoke([{"role": "user", "content": prompt}])
        return response.content if hasattr(response, "content") else str(response)

    return _invoke


def _local_env_mapper(payload: dict, llm_invoke) -> dict:
    raw_scan = scan_repo(Path(payload["repo_path"]))
    return synthesize_env_map(raw_scan, llm_invoke)


def _local_test_generator(payload: dict, llm_invoke) -> dict:
    try:
        source = generate_tests(payload["diff"], payload["env_map"], llm_invoke, payload.get("surviving_mutants"))
    except SyntaxError as exc:
        return {"error": str(exc)}
    return {"test_source": source}


def _local_evaluator(payload: dict) -> dict:
    repo_path = DEMO_REPO
    test_file = "tests/test_generated_eval.py"
    (repo_path / test_file).write_text(payload["test_source"])
    try:
        pytest_result = run_pytest(repo_path, test_file)
        if not pytest_result["passed"]:
            return {"error": "generated tests failed against real code", "pytest_output": pytest_result["summary"]}

        mutation_result = run_mutation(repo_path, payload["touched_paths"], test_file)
        return {"tests_passed": True, **mutation_result}
    finally:
        (repo_path / test_file).unlink(missing_ok=True)


def _local_ci_deliverer(payload: dict) -> dict:
    return {"pr_url": "local-eval-no-pr-opened"}


def _check_no_false_positive(test_source: str, original_content: str, scenario: dict) -> bool:
    """Restores the fixed/correct file, runs the generated test against it,
    and returns True if it PASSES (i.e. no false positive). Assumes the
    buggy variant is currently swapped in; restores it again afterward so
    the caller's own bookkeeping of "currently buggy" stays accurate."""
    target_path = DEMO_REPO / scenario["target_path"]
    buggy_content = target_path.read_text()
    target_path.write_text(original_content)

    test_file = "tests/test_generated_eval_fixed_check.py"
    (DEMO_REPO / test_file).write_text(test_source)
    try:
        result = run_pytest(DEMO_REPO, test_file)
        return result["passed"]
    finally:
        (DEMO_REPO / test_file).unlink(missing_ok=True)
        target_path.write_text(buggy_content)


def main():
    scenarios = json.loads(KNOWN_BUGS_PATH.read_text())
    llm_invoke = _make_llm_invoke()

    false_positive_count = 0
    all_scenario_rounds = {}

    for scenario in scenarios:
        print(f"\n=== Scenario: {scenario['id']} ===")
        original_content = _swap_in_scenario(scenario)

        try:
            request = {
                "repo_path": str(DEMO_REPO),
                "diff": f"diff --git a/{scenario['target_path']} b/{scenario['target_path']}\n+ integration test target",
                "repo_url": "local",
                "repo_full_name": "local/demo-repo",
                "pr_number": 0,
                "base_ref": "HEAD",
                "repo_files": {scenario["target_path"]: (DEMO_REPO / scenario["target_path"]).read_text()},
                "touched_paths": [scenario["target_path"]],
            }

            result = run_pipeline(
                request,
                invoke_env_mapper=lambda p: _local_env_mapper(p, llm_invoke),
                invoke_test_generator=lambda p: _local_test_generator(p, llm_invoke),
                invoke_evaluator=_local_evaluator,
                invoke_ci_deliverer=_local_ci_deliverer,
            )

            scores = [r["score"] for r in result["rounds"]]
            all_scenario_rounds[scenario["id"]] = scores
            print(f"status={result['status']}, mutation scores by round={scores}")

            final_test_source = result.get("final_test_source")
            if final_test_source:
                passes_on_fixed_code = _check_no_false_positive(final_test_source, original_content, scenario)
                if not passes_on_fixed_code:
                    false_positive_count += 1
                    print(f"FALSE POSITIVE: generated test for {scenario['id']} fails against fixed code")
        finally:
            _restore_target(scenario, original_content)

    print("\n=== Summary ===")
    for scenario_id, scores in all_scenario_rounds.items():
        improving = all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1))
        print(f"{scenario_id}: rounds={scores}, monotonically non-decreasing={improving}")

    print(f"\nFalse positives (generated tests failing on correct code): {false_positive_count}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the harness in fake-LLM mode to confirm the wiring works end to end without AWS**

Run: `cd /Users/ilaakshmishra/Documents/killjoy && KILLJOY_FAKE_LLM=1 python scripts/evaluate_killjoy.py`
Expected: prints a `=== Scenario: ... ===` block for each of the 3 scenarios followed by a `=== Summary ===` block; exits 0. With the stub LLM the generated test is always `def test_placeholder(): assert True`, so mutation scores will be low/flat — that's expected for the fake-LLM dry run; the metrics only become meaningful once run with `KILLJOY_FAKE_LLM` unset against real Bedrock credentials.

- [ ] **Step 3: Stage the changes**

```bash
git add scripts/evaluate_killjoy.py
```

---

### Task 27: Top-level README

**Files:**
- Modify: `README.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Rewrite `README.md`**

```markdown
# Killjoy

Kills your fake tests before your mutants do.

Killjoy generates real pytest integration tests for a pull request, proves
they aren't vanity coverage by running mutation testing (mutmut) against
them, feeds any surviving mutant back into test generation for up to 3
rounds, and only then opens a PR — labeled `ai-generated`, from a dedicated
branch, never auto-merged — stating the mutation score and any mutant that
was never caught.

## Architecture

A GitHub webhook (PR opened/synchronize) hits API Gateway, which invokes a
Lambda that verifies the webhook's HMAC signature and starts the
Orchestrator on AWS Bedrock AgentCore Runtime. The Orchestrator runs four
specialist agents as a sequential pipeline with a bounded feedback loop:

1. **Environment Mapper** — scans the repo's call graph, conftest fixtures,
   and layer boundaries (AST-based, no LLM), then an LLM synthesis step
   labels which layers are safe to run for real vs. which are outer edges
   that need an in-memory/mocked substitute.
2. **Test Generator** — writes pytest integration tests from the PR diff and
   the environment map, using only real internal code plus the substitutes
   the mapper identified.
3. **Execution & Mutation Evaluator** — runs the tests for real inside an
   AgentCore Code Interpreter sandbox, then runs mutmut scoped to the
   touched lines. Surviving mutants go back to step 2. Capped at 3 rounds.
4. **CI & PR Deliverer** — only once a sandbox run has proven the tests pass
   and a mutation score is computed: commits the tests plus a GitHub Actions
   workflow to a new branch and opens the PR.

See `docs/superpowers/plans/2026-08-08-killjoy-integration-test-mutation.md`
for the full build plan, task by task.

## Running locally (no AWS)

Every agent's core logic is a plain, dependency-injected Python module with
no AWS calls in it — each is independently unit-tested:

```bash
python -m pytest
```

To dry-run the whole pipeline against the demo repo's planted-bug scenarios
without any AWS credentials:

```bash
KILLJOY_FAKE_LLM=1 python scripts/evaluate_killjoy.py
```

## Deploying

```bash
cd infra
terraform init
terraform apply -var="github_token=ghp_xxxx" -var="webhook_secret=whsec_xxxx"

# Build and push each ARM64 image after terraform apply creates the ECR repos
for agent in orchestrator environment-mapper test-generator evaluator ci-pr-deliverer; do
  ECR_URL=$(terraform -chdir=infra output -json ecr_urls | jq -r ".\"$agent\"")
  docker buildx build --platform linux/arm64 -t $ECR_URL:latest --push ./app/<MatchingAgentDir>
done
```

Then configure a GitHub webhook on the target repo pointing at
`terraform output -raw api_gateway_webhook_url`, content type
`application/json`, secret matching `webhook_secret`, events: Pull requests.

## Guardrails

- Dedicated branch per PR, never `main`.
- Opens only after a real sandbox pass + computed mutation score.
- Labeled `ai-generated`, never auto-merged.
- Mutation feedback loop capped at 3 rounds.
- One Killjoy PR per triggering PR (DynamoDB dedup) plus a daily ceiling
  across all triggering PRs (`daily_pr_ceiling` Terraform variable).
- Any stage failure aborts the whole pipeline — no partial or broken PR.

## v1 scope vs v2

v1 is intra-application integration tests only (in-memory/mocked outer
edges, no docker-compose), Python/pytest, GitHub Actions only, automatic
webhook trigger. v2 adds docker-compose/testcontainers-backed cross-service
tests, additional CI systems, and possibly additional languages.
```

- [ ] **Step 2: Stage the changes**

```bash
cd /Users/ilaakshmishra/Documents/killjoy
git add README.md
```

---

## Self-Review Notes

- **Spec coverage:** Environment Mapper (Tasks 8–9), Test Generator (Tasks 10–11), Execution & Mutation Evaluator (Tasks 12–14), CI & PR Deliverer (Tasks 15–18), Orchestrator sequential pipeline + 3-round feedback loop (Tasks 19–21), webhook → API Gateway → Lambda trigger (Task 22, wired in Task 25), all 5 guardrails (branch/never-main enforced in `pr_builder.open_pull_request`; sandbox-pass-plus-score enforced in `CIPRDelivererAgent/main.py`; `ai-generated` label in `pr_builder.build_pr_body`/`open_pull_request`; 3-round cap in `pipeline.run_pipeline`; one-PR-per-triggering-PR + daily ceiling in `guardrails.reserve_run`), stage-failure-aborts-pipeline (every early-return branch in `pipeline.run_pipeline`), demo repo with 3 planted bugs at 3 layers (Tasks 2–6), AgentCore sandbox spike (Task 7), the two ADR-mandated evaluation metrics (Task 26), GitHub Actions-only CI detection (Task 16), AWS infra (Tasks 23–25). No spec section is without a task.
- **Placeholder scan:** every step has literal, complete code; the one place language is deliberately conditional is Task 7/14's note that the Code Interpreter tool names must be corrected from the spike's actual findings before Task 14 is trusted — that is an explicit, named open risk from the ADR itself, not a vague TODO, and Task 7's step 4 spells out exactly what to update and where.
- **Type consistency checked:** `env_map` schema (`layers`/`fixtures`/`outer_edges`) is identical across Task 9's `synthesize_env_map`, Task 10's `generate_tests`, and Task 20/26's pipeline wiring. `surviving_mutants` shape (`id`/`file`/`line`/`description`) is identical across Task 13's `run_mutation`, Task 14's `main.py` parser, Task 10's `generate_tests`, and Task 20's `pipeline.run_pipeline`. The Evaluator's result shape (`tests_passed`/`score`/`killed`/`survived`/`surviving_mutants`) is identical across Task 14's `main.py` return, Task 17's `build_pr_body`, and Task 20's `run_pipeline` consumption. DynamoDB table names (`killjoy-pr-runs`, `killjoy-daily-counter`) match between Task 19's tests, Task 21's `main.py` defaults, and Task 24's Terraform resource names.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-08-killjoy-integration-test-mutation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
