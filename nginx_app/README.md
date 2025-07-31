# Nginx App

Provides management of nginx configurations with support for HTTP, WebSockets, optional SSL
certificates and fallback upstream servers. Configurations can be applied to the host using a
management command:

```bash
python manage.py apply_nginx_config <id>
```

The Django admin includes an action to test connectivity to the configured upstream servers.
It also shows the rendered configuration so it can be reviewed or copied.
