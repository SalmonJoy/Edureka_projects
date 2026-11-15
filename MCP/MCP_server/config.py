"""Local API addresses and MCP server settings."""

import os

EMPLOYEE_API_URL = os.getenv("EMPLOYEE_API_URL", "http://127.0.0.1:8000").rstrip("/")
POLICY_API_URL = os.getenv("POLICY_API_URL", "http://127.0.0.1:8001").rstrip("/")
LEAVE_API_URL = os.getenv("LEAVE_API_URL", "http://127.0.0.1:8002").rstrip("/")

MCP_HOST = "127.0.0.1"
MCP_PORT = int(os.getenv("MCP_PORT", "8003"))
MCP_URL = f"http://{MCP_HOST}:{MCP_PORT}/mcp"
REQUEST_TIMEOUT = 10.0
