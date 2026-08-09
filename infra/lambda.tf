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
        Effect = "Allow"
        Action = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = [
          aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn,
          "${aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn}/runtime-endpoint/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.webhook_handler.arn]
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
    }
  }
}
