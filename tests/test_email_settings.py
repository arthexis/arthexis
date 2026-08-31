from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EMAIL_ENV_KEYS = {
    "DEFAULT_ADMIN_EMAIL",
    "DEFAULT_FROM_EMAIL",
    "DJANGO_SECRET_KEY",
    "EMAIL_BACKEND",
    "EMAIL_HOST",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_HOST_USER",
    "EMAIL_PORT",
    "EMAIL_USE_SSL",
    "EMAIL_USE_TLS",
    "SECRET_KEY",
    "SERVER_EMAIL",
}

SETTINGS_SCRIPT = r"""
import json
import config.settings.base as settings

print(json.dumps({
    "EMAIL_BACKEND": settings.EMAIL_BACKEND,
    "EMAIL_HOST": settings.EMAIL_HOST,
    "EMAIL_PORT": settings.EMAIL_PORT,
    "EMAIL_HOST_USER": settings.EMAIL_HOST_USER,
    "EMAIL_HOST_PASSWORD": settings.EMAIL_HOST_PASSWORD,
    "EMAIL_USE_TLS": settings.EMAIL_USE_TLS,
    "EMAIL_USE_SSL": settings.EMAIL_USE_SSL,
    "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
    "SERVER_EMAIL": settings.SERVER_EMAIL,
}, sort_keys=True))
"""


def _load_email_settings(extra_env: dict[str, str] | None = None) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in EMAIL_ENV_KEYS:
        env.pop(key, None)
    env["DJANGO_SECRET_KEY"] = "test-secret"
    env.update(extra_env or {})

    result = subprocess.run(
        [sys.executable, "-c", SETTINGS_SCRIPT],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_email_settings_default_to_local_smtp():
    assert _load_email_settings() == {
        "DEFAULT_FROM_EMAIL": "noreply@example.com",
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": "localhost",
        "EMAIL_HOST_PASSWORD": "",
        "EMAIL_HOST_USER": "",
        "EMAIL_PORT": 25,
        "EMAIL_USE_SSL": False,
        "EMAIL_USE_TLS": False,
        "SERVER_EMAIL": "noreply@example.com",
    }


def test_email_settings_read_gmail_smtp_environment():
    assert _load_email_settings(
        {
            "DEFAULT_ADMIN_EMAIL": "arthexis@gmail.com",
            "EMAIL_HOST": "smtp.gmail.com",
            "EMAIL_HOST_PASSWORD": "app-password",
            "EMAIL_HOST_USER": "arthexis@gmail.com",
            "EMAIL_PORT": "587",
            "EMAIL_USE_TLS": "true",
        }
    ) == {
        "DEFAULT_FROM_EMAIL": "arthexis@gmail.com",
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": "smtp.gmail.com",
        "EMAIL_HOST_PASSWORD": "app-password",
        "EMAIL_HOST_USER": "arthexis@gmail.com",
        "EMAIL_PORT": 587,
        "EMAIL_USE_SSL": False,
        "EMAIL_USE_TLS": True,
        "SERVER_EMAIL": "arthexis@gmail.com",
    }


def test_email_settings_ignore_non_integer_port():
    assert _load_email_settings({"EMAIL_PORT": "not-a-port"})["EMAIL_PORT"] == 25


def test_email_settings_ignore_out_of_range_ports():
    assert _load_email_settings({"EMAIL_PORT": "0"})["EMAIL_PORT"] == 25
    assert _load_email_settings({"EMAIL_PORT": "65536"})["EMAIL_PORT"] == 25
