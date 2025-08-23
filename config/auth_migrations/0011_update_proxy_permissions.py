from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0011_update_proxy_permissions').Migration  # type: ignore
