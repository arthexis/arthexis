# Nodes App

The `nodes` app exposes a simple JSON interface for keeping track of other instances of this project:

- `POST /nodes/register/` with `hostname`, `address` and optional `port` will register or update the node.
- `GET /nodes/list/` returns all known nodes.
- `GET /nodes/screenshot/` captures a screenshot of the site and records it for the current node.

## NGINX Templates

The `NginxConfig` model manages NGINX templates with support for HTTP, WebSockets,
optional SSL certificates and fallback upstream servers. Templates can be applied
to the host using a management command:

```bash
python manage.py apply_nginx_config <id>
```

The Django admin includes an action to test connectivity to the configured
upstream servers and shows the rendered template for review.
