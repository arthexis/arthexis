from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0008_alter_user_username_max_length').Migration  # type: ignore
