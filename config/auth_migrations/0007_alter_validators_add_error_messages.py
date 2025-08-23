from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0007_alter_validators_add_error_messages').Migration  # type: ignore
