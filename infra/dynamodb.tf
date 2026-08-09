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
