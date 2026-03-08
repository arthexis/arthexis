"""Unit tests for HTTPS certificate provisioning flow."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.core.management.base import CommandError

from apps.certs.services import CertbotError
from apps.nginx.management.commands.https_parts import certificate_flow


@dataclass
class _DummyStyle:
    """Mimic Django style helpers used by command services."""

    def WARNING(self, message: str) -> str:  # noqa: N802
        return message


@dataclass
class _DummyStdout:
    """Capture writes emitted by command services."""

    lines: list[str]

    def write(self, message: str) -> None:
        self.lines.append(message)


@dataclass
class _DummyService:
    """Minimal HTTPS service shell for certificate flow unit tests."""

    stdout: _DummyStdout
    style: _DummyStyle


class _DummyCertificate:
    """Certificate stub that tracks whether provisioning is invoked."""

    def __init__(self) -> None:
        self.provision_called = False

    def provision(self, **kwargs) -> None:  # noqa: ANN003
        self.provision_called = True


def test_provision_certificate_fails_fast_when_certbot_is_missing(monkeypatch):
    """Regression: HTTPS provisioning should fail before attempting certificate requests when certbot is missing."""

    service = _DummyService(stdout=_DummyStdout(lines=[]), style=_DummyStyle())
    certificate = _DummyCertificate()

    monkeypatch.setattr(
        certificate_flow,
        "ensure_certbot_available",
        lambda *, sudo="sudo": (_ for _ in ()).throw(
            CertbotError("sudo: certbot: command not found\napt install -y certbot")
        ),
    )

    with pytest.raises(CommandError, match="apt install -y certbot"):
        certificate_flow._provision_certificate(
            service,
            domain="example.com",
            config=object(),
            certificate=certificate,
            use_local=False,
            use_godaddy=True,
            sandbox_override=None,
            sudo="sudo",
            reload=True,
            force_renewal=False,
        )

    assert certificate.provision_called is False
