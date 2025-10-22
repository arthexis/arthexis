import os

import django
from django.urls import resolve


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def test_core_url_autodiscovered():
    match = resolve("/api/rfid/rfid-login/")
    assert match.view_name == "rfid-login"


def test_rfid_url_autodiscovered():
    match = resolve("/ocpp/rfid/validator/")
    assert match.view_name == "rfid-reader"
