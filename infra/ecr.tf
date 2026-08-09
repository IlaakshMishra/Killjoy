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
