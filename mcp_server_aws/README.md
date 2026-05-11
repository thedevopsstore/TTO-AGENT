# mcp_server_aws

A FastMCP wrapper that exposes the upstream [AWS MCP](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html) tool surface to agents with automatic per-account credential routing.

Every tool call requires an `account_id`. The wrapper assumes a fixed IAM role in the target account via STS, caches the credentials, and signs the upstream call using `mcp-proxy-for-aws` library mode. No allowlist file to maintain — access is controlled entirely by IAM trust policies.

## How it works

```
Agent
  └─ call_tool(tool_name, {account_id: "111122223333", ...})
       └─ AccountRoutingMiddleware
            ├─ validate account_id format (12 digits)
            ├─ STS AssumeRole → arn:aws:iam::111122223333:role/McpExecutionRole
            ├─ cache credentials (refresh near expiry, invalidate on 403)
            └─ aws_iam_streamablehttp_client(credentials=...) → AWS MCP endpoint
```

Tool discovery (`list_tools`) uses a persistent subprocess via `mcp-proxy-for-aws` with the server's bootstrap/instance role. Tool execution uses a per-call authenticated client with the assumed account credentials.

## Prerequisites

- Python 3.11+
- `uv` package manager
- AWS credentials available in the environment (instance role on ECS/AgentCore, or a configured profile locally)
- Each target account must have a role (default: `McpExecutionRole`) that trusts the wrapper's execution role

## Configuration

Copy `.env.example` to `.env` and set values:

| Variable | Default | Description |
|---|---|---|
| `AWS_MCP_ENDPOINT` | required | Upstream AWS MCP URL, e.g. `https://aws-mcp.us-east-1.api.aws/mcp` |
| `AWS_MCP_REGION` | `us-east-1` | AWS region for SigV4 signing |
| `AWS_MCP_SERVICE` | auto | Inferred from URL if unset |
| `AWS_ROLE_NAME` | `McpExecutionRole` | Role name assumed in every target account |
| `AWS_BOOTSTRAP_PROFILE` | instance role | AWS profile for tool discovery; unset on ECS/AgentCore |
| `STS_REFRESH_WINDOW_SECONDS` | `300` | Re-assume role when fewer than this many seconds remain |
| `WRAPPER_HOST` | `0.0.0.0` | |
| `WRAPPER_PORT` | `8000` | |
| `WRAPPER_LOG_LEVEL` | `INFO` | |

## Run

```bash
uv run mcp-server-aws
```

Or directly:

```bash
uv run python -m mcp_server_aws.server
```

## IAM setup

The wrapper assumes `arn:aws:iam::<account_id>:role/<AWS_ROLE_NAME>` for every tool call.

**Wrapper execution role** needs:
```json
{
  "Effect": "Allow",
  "Action": "sts:AssumeRole",
  "Resource": "arn:aws:iam::*:role/McpExecutionRole"
}
```

**Each target account's role** (`McpExecutionRole`) needs a trust policy:
```json
{
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::<wrapper-account-id>:role/<wrapper-execution-role>"
  },
  "Action": "sts:AssumeRole"
}
```

Adding a new target account requires only creating this role in that account — no code or config changes.

## Agent usage

Every upstream AWS MCP tool is exposed with an additional required argument:

```
account_id  (string, 12-digit AWS account ID)
```

Example tool call from an agent:
```json
{
  "tool": "aws___list_s3_buckets",
  "arguments": {
    "account_id": "111122223333"
  }
}
```

## Project structure

```
mcp_server_aws/
├── config.py       Settings (pydantic-settings, reads env/.env)
├── credentials.py  STS AssumeRole cache per account_id
├── upstream.py     Per-call authenticated MCP client
├── middleware.py   account_id injection and call routing
└── server.py       FastMCP proxy + middleware + server startup
```
