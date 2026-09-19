"""WSGI configuration for Arthexis."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")

application = get_wsgi_application()
