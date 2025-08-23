from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0006_require_contenttypes_0002').Migration  # type: ignore
