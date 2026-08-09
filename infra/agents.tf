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
    GITHUB_SECRET_ARN   = aws_secretsmanager_secret.github_token.arn
    ENV_MAPPER_ARN      = aws_bedrockagentcore_agent_runtime.environment_mapper.agent_runtime_arn
    TEST_GENERATOR_ARN  = aws_bedrockagentcore_agent_runtime.test_generator.agent_runtime_arn
    EVALUATOR_ARN       = aws_bedrockagentcore_agent_runtime.evaluator.agent_runtime_arn
    CI_PR_DELIVERER_ARN = aws_bedrockagentcore_agent_runtime.ci_pr_deliverer.agent_runtime_arn
    PR_RUNS_TABLE       = aws_dynamodb_table.pr_runs.name
    DAILY_COUNTER_TABLE = aws_dynamodb_table.daily_counter.name
    DAILY_PR_CEILING    = tostring(var.daily_pr_ceiling)
  }

  depends_on = [
    aws_bedrockagentcore_agent_runtime.environment_mapper,
    aws_bedrockagentcore_agent_runtime.test_generator,
    aws_bedrockagentcore_agent_runtime.evaluator,
    aws_bedrockagentcore_agent_runtime.ci_pr_deliverer,
  ]
}
