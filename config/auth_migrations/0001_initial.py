from importlib import import_module
Migration = import_module('django.contrib.auth.migrations.0001_initial').Migration  # type: ignore
