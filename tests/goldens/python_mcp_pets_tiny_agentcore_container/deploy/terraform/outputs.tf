output "runtime_arn" {
  description = "ARN of the AgentCore runtime."
  value       = aws_bedrockagentcore_agent_runtime.server.agent_runtime_arn
}

output "mcp_url" {
  description = "URL MCP clients connect to (send Authorization: Bearer <token>)."
  value       = "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(aws_bedrockagentcore_agent_runtime.server.agent_runtime_arn)}/invocations?qualifier=DEFAULT"
}
