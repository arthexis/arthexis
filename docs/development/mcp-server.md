# MCP server for Arthexis operational tools

The suite exposes selected in-process operations as MCP tools over stdio.

## Command ownership and compatibility

The canonical MCP CLI command implementations live in `apps/mcp/management/commands/`:

- `mcp_server` → `apps.mcp.management.commands.mcp_server`
- `create_mcp_api_key` → `apps.mcp.management.commands.create_mcp_api_key`

Legacy compatibility shims still exist in `apps.core.management.commands` and emit deprecation warnings. Update any direct Python imports to use the `apps.mcp` paths as the source of truth.

## 1) Start the server

```bash
python manage.py mcp_server
```

The command runs a JSON-RPC MCP loop on stdio.

## 2) Configure exposed tools

You can configure an allow/deny list from CLI flags:

```bash
# Allow specific MCP tools
python manage.py mcp_server --allow arthexis.graphql.query

# Or deny specific tools from the full set
python manage.py mcp_server --deny arthexis.auth.whoami
```

Or from environment variables:

- `ARTHEXIS_MCP_TOOLS_ALLOW`
- `ARTHEXIS_MCP_TOOLS_DENY`

Values are comma-separated tool names.

## 3) Available built-in tools

- `arthexis.graphql.query`:
  Executes GraphQL operations inside Django with the authenticated user as the GraphQL context.
- `arthexis.auth.whoami`:
  Returns identity and group details for the authenticated MCP API key owner.

## 4) External agent configuration

Register the process in your MCP client/agent host so it launches:

```json
{
  "mcpServers": {
    "arthexis": {
      "command": "python",
      "args": ["manage.py", "mcp_server", "--allow", "arthexis.graphql.query"]
    }
  }
}
```

## 5) API keys and security groups

Every `tools/call` request must include an `api_key` argument. Generate per-user keys with:

```bash
python manage.py create_mcp_api_key --username <username> --label "agent"
```

Tool access requires a valid, non-expired key. Some tools may also require membership in one of the tool's configured security groups.

Key defaults:

- `--expires-in-days` defaults to `90`
- use `--expires-in-days 0` for non-expiring keys
