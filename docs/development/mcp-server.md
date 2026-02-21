# MCP server for remote Django commands

The suite can expose selected Django management commands as MCP tools over stdio.

## Command ownership and compatibility

The canonical MCP CLI command implementations live in `apps/mcp/management/commands/`:

- `mcp_server` → `apps.mcp.management.commands.mcp_server`
- `create_mcp_api_key` → `apps.mcp.management.commands.create_mcp_api_key`

Legacy compatibility shims still exist in `apps.core.management.commands` and emit deprecation warnings. Update any direct Python imports to use the `apps.mcp` paths as the source of truth.

## 1) Mark commands as remote

Use `@remote_command` on a management command class:

```python
from apps.mcp.remote_commands import remote_command

@remote_command(
    description="Display suite uptime and lock status.",
    security_groups=["ops"],  # Optional: omit to allow any valid key
)
class Command(BaseCommand):
    ...
```

Only decorated commands are discoverable by the MCP server.

## 2) Start the server

```bash
python manage.py mcp_server
```

The command runs a JSON-RPC MCP loop on stdio.

## 3) Configure exposed commands

You can configure an allow/deny list from CLI flags:

```bash
# Allow specific commands
python manage.py mcp_server --allow uptime

# Or deny specific commands from the full set
python manage.py mcp_server --deny some-dangerous-command
```

Or from environment variables:

- `ARTHEXIS_MCP_REMOTE_ALLOW`
- `ARTHEXIS_MCP_REMOTE_DENY`

Values are comma-separated Django command names.

## 4) External agent configuration

Register the process in your MCP client/agent host so it launches:

```json
{
  "mcpServers": {
    "arthexis": {
      "command": "python",
      "args": ["manage.py", "mcp_server", "--allow", "uptime"]
    }
  }
}
```

The server exposes tools named:

- `django.command.uptime`

(and any other decorated command).


## 5) API keys and command security groups

Every `tools/call` request must include an `api_key` argument. Generate per-user keys with:

```bash
python manage.py create_mcp_api_key --username <username> --label "agent"
```

Default behavior: decorated commands are callable by any user who presents a valid, non-expired key.

To restrict a command to specific security groups, pass `security_groups` to `@remote_command`. A user only needs membership in one listed group.

Key defaults:

- `--expires-in-days` defaults to `90`
- use `--expires-in-days 0` for non-expiring keys
