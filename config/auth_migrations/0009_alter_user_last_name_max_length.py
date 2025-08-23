from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0009_alter_user_last_name_max_length').Migration  # type: ignore
