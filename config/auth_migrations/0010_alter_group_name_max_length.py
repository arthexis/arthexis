from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0010_alter_group_name_max_length').Migration  # type: ignore
