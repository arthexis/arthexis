import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import django
from django.apps import apps

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

_original_setup = django.setup

def safe_setup():
    if not apps.ready:
        _original_setup()

django.setup = safe_setup
safe_setup()
