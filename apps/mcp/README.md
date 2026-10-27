# DataPilot MCP server

Read-only tools (12): `list_approved_metrics`, `get_metric_definition`, `ask_data` (runs a governed question), `preview_analytical_query` (shows the plan and SQL without running it), `get_dashboard`, `list_data_sources`, `list_datasets`, `get_dataset_profile`, `get_data_quality_summary`, `get_pipeline_status`, `search_knowledge`, `ask_knowledge`. Ids given to a tool must be plain ids; anything else is refused before a request is made.

    pip install "mcp<2" httpx   # the server uses the v1 FastMCP API; mcp 2.x renamed it, so a port is needed before allowing 2.x
    # get a token: POST /api/v1/auth/login
    set DATAPILOT_API_URL=http://localhost:8000      (PowerShell: $env:DATAPILOT_API_URL="...")
    set DATAPILOT_TOKEN=<token>
    python apps/mcp/server.py

Claude Desktop / other MCP clients (stdio) config example:

    { "mcpServers": { "datapilot": { "command": "python", "args": ["apps/mcp/server.py"],
        "env": { "DATAPILOT_API_URL": "http://localhost:8000", "DATAPILOT_TOKEN": "<token>" } } } }

Tokens expire (30 min by default). One server instance = one user and tenant; there is no tenant parameter.
Not implemented: write tools, raw SQL, OAuth for remote MCP. Verify the config format against your client's docs.
