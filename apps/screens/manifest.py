"""Manifest entries for Django app loading."""

DJANGO_APPS: list[str] = []
OPTIONAL_DJANGO_APPS = [
    "apps.screens",
]

REQUIRES_APPS = [
    "apps.sensors",
    "apps.summary",
]
