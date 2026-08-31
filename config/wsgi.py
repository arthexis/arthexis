"""WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from config.loadenv import loadenv
from config.sqlite_driver import bootstrap_sqlite_driver

loadenv()
bootstrap_sqlite_driver()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application

from apps.core.checks.apps_registry import enforce_apps_registry_configuration

application = get_wsgi_application()
enforce_apps_registry_configuration()
