from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0002_alter_permission_name_max_length').Migration  # type: ignore
