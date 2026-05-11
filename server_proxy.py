"""Simple MCP proxy server that forwards all requests to AWS MCP via subprocess."""

import os

from fastmcp.server import create_proxy
from fastmcp.client.transports import StdioTransport

ENDPOINT = os.environ.get("AWS_MCP_ENDPOINT", "https://aws-mcp.us-east-1.api.aws/mcp")
REGION = os.environ.get("AWS_REGION", "us-east-1")
PROFILE = os.environ.get("AWS_PROFILE")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

args = ["mcp-proxy-for-aws@latest", ENDPOINT, "--region", REGION]
if PROFILE:
    args += ["--profile", PROFILE]

server = create_proxy(
    StdioTransport(command="uvx", args=args),
    name="aws-mcp-proxy",
)

if __name__ == "__main__":
    server.run(transport="streamable-http", host=HOST, port=PORT)
