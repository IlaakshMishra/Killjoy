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
