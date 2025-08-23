from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0003_alter_user_email_max_length').Migration  # type: ignore
