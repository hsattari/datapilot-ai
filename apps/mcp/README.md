# DataPilot MCP server

Read-only tools: `list_approved_metrics`, `ask_data`, `get_dashboard`, `list_datasets`, `search_knowledge`, `ask_knowledge`.

    pip install mcp httpx
    # get a token: POST /api/v1/auth/login
    set DATAPILOT_API_URL=http://localhost:8000      (PowerShell: $env:DATAPILOT_API_URL="...")
    set DATAPILOT_TOKEN=<token>
    python apps/mcp/server.py

Claude Desktop / other MCP clients (stdio) config example:

    { "mcpServers": { "datapilot": { "command": "python", "args": ["apps/mcp/server.py"],
        "env": { "DATAPILOT_API_URL": "http://localhost:8000", "DATAPILOT_TOKEN": "<token>" } } } }

Tokens expire (30 min by default). One server instance = one user and tenant; there is no tenant parameter.
Not implemented: write tools, raw SQL, OAuth for remote MCP. Verify the config format against your client's docs.
