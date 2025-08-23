from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0004_alter_user_username_opts').Migration  # type: ignore
