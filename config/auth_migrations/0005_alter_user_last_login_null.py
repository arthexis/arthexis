from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0005_alter_user_last_login_null').Migration  # type: ignore
