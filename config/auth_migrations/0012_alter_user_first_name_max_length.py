from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0012_alter_user_first_name_max_length').Migration  # type: ignore
