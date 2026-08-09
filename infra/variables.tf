variable "aws_region" {
  default = "us-east-1"
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
