"""Manifest entries for Django app loading."""

DJANGO_APPS: list[str] = []
OPTIONAL_DJANGO_APPS = [
    "apps.odoo",
]

REQUIRES_APPS = [
    "apps.discovery",
]
