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
