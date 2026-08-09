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
