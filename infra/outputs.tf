output "orchestrator_arn" {
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
  description = "Set as ORCHESTRATOR_ARN for the webhook Lambda"
}

output "api_gateway_webhook_url" {
  value = "${aws_apigatewayv2_api.webhook.api_endpoint}/webhook"
}
