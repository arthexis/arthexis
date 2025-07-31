import os

import django
from django.urls import resolve


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def test_accounts_url_autodiscovered():
    match = resolve("/accounts/rfid-login/")
    assert match.view_name == "rfid-login"


def test_qrcodes_custom_prefix():
    match = resolve("/qr/")
    assert match.view_name == "qrcodes:generator"

