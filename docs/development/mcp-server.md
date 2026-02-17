# MCP server for remote Django commands

The suite can expose selected Django management commands as MCP tools over stdio.

## 1) Mark commands as remote

Use `@remote_command` on a management command class:

```python
from apps.core.mcp.remote_commands import remote_command

@remote_command(description="Display suite uptime and lock status.")
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
python manage.py mcp_server --allow uptime,redis --deny redis
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
      "args": ["manage.py", "mcp_server", "--allow", "uptime,redis"]
    }
  }
}
```

The server exposes tools named:

- `django.command.uptime`
- `django.command.redis`

(and any other decorated command).
