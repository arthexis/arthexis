"""Helpers for upgrade completion notifications."""

from __future__ import annotations

import json
import logging
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.validators import ValidationError
from django.utils import timezone

from apps.emails.utils import normalize_recipients
from apps.users import temp_passwords
from apps.users.system import ensure_default_admin_user


logger = logging.getLogger(__name__)


@dataclass
class UpgradeNotificationResult:
    """Outcome from a single upgrade completion notification attempt."""

    status: str
    exit_status: int
    email_sent: bool
    subject: str
    recipients: list[str] = field(default_factory=list)
    admin_username: str = ""
    temp_password: str = ""
    expires_at: datetime | None = None
    error: str = ""


def _resolve_local_node():
    try:
        from apps.nodes.models import Node
    except Exception:
        return None

    try:
        return Node.get_local()
    except Exception:
        logger.warning("Unable to resolve local node for upgrade notification", exc_info=True)
        return None


def _read_version_marker(base_dir: Path) -> str:
    version_path = base_dir / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_current_revision(base_dir: Path) -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=base_dir,
                stderr=subprocess.STDOUT,
                text=True,
            )
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""


def _short_revision(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "-"
    return normalized[:12]


def _load_upgrade_duration_metadata(base_dir: Path) -> dict[str, object]:
    lock_path = base_dir / ".locks" / "upgrade_duration.lck"
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_instance_label(base_dir: Path, *, service_name: str | None = None) -> tuple[str, str]:
    node = _resolve_local_node()
    hostname = socket.gethostname() or "unknown-host"
    if node is not None:
        try:
            domain = node.get_base_domain()
        except Exception:
            domain = ""
        label = (
            domain
            or getattr(node, "hostname", "")
            or service_name
            or base_dir.name
            or hostname
        )
        return label, getattr(node, "hostname", "") or hostname

    label = service_name or base_dir.name or hostname
    return label, hostname


def _send_secret_email(
    subject: str,
    body: str,
    recipient_list: list[str],
) -> None:
    node = _resolve_local_node()
    outbox = None
    if node is not None:
        try:
            outbox = getattr(node, "email_outbox", None)
        except Exception:
            outbox = None

    sender = (
        getattr(outbox, "from_email", "") or settings.DEFAULT_FROM_EMAIL or settings.DEFAULT_ADMIN_EMAIL
    )
    if outbox is not None and getattr(outbox, "is_enabled", False):
        connection = outbox.get_connection()
    else:
        connection = get_connection(getattr(settings, "EMAIL_BACKEND", None))

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=sender,
        to=recipient_list,
        connection=connection,
    )
    email.send(fail_silently=False)


def notify_upgrade_completion(
    *,
    base_dir: Path | str | None = None,
    exit_status: int,
    source: str = "upgrade.sh",
    channel: str | None = None,
    branch: str | None = None,
    service_name: str | None = None,
    initial_version: str | None = None,
    target_version: str | None = None,
    initial_revision: str | None = None,
    target_revision: str | None = None,
) -> UpgradeNotificationResult:
    """Send the configured upgrade completion email and rotate the temp admin password."""

    resolved_base_dir = Path(base_dir) if base_dir is not None else Path(settings.BASE_DIR)
    status = "succeeded" if int(exit_status) == 0 else "failed"
    label, hostname = _resolve_instance_label(resolved_base_dir, service_name=service_name)
    subject = f"Upgrade {status}: {label}"

    try:
        recipients = normalize_recipients(
            [getattr(settings, "DEFAULT_ADMIN_EMAIL", "")],
            validate=True,
        )
    except ValidationError as exc:
        return UpgradeNotificationResult(
            status=status,
            exit_status=int(exit_status),
            email_sent=False,
            subject=subject,
            error=str(exc),
        )

    if not recipients:
        return UpgradeNotificationResult(
            status=status,
            exit_status=int(exit_status),
            email_sent=False,
            subject=subject,
            error="No DEFAULT_ADMIN_EMAIL is configured for upgrade notifications.",
        )

    ensured = ensure_default_admin_user(record_updates=True)
    if ensured is None:
        return UpgradeNotificationResult(
            status=status,
            exit_status=int(exit_status),
            email_sent=False,
            subject=subject,
            recipients=recipients,
            error="DEFAULT_ADMIN_USERNAME resolved to an empty value.",
        )

    admin_user, admin_updates = ensured
    password = temp_passwords.generate_password()
    expires_at = timezone.now() + temp_passwords.DEFAULT_EXPIRATION
    temp_passwords.store_temp_password(
        admin_user.username,
        password,
        expires_at,
        allow_change=True,
    )

    current_version = _read_version_marker(resolved_base_dir)
    current_revision = _read_current_revision(resolved_base_dir)
    duration = _load_upgrade_duration_metadata(resolved_base_dir)

    body_lines = [
        f"Upgrade status: {status}",
        f"Source: {source}",
        f"Exit status: {int(exit_status)}",
        f"Instance: {label}",
        f"Hostname: {hostname}",
        f"Install dir: {resolved_base_dir}",
    ]
    if service_name:
        body_lines.append(f"Service: {service_name}")
    if channel:
        body_lines.append(f"Channel: {channel}")
    if branch:
        body_lines.append(f"Branch: {branch}")
    if initial_version:
        body_lines.append(f"Initial version: {initial_version}")
    if target_version:
        body_lines.append(f"Target version: {target_version}")
    if current_version:
        body_lines.append(f"Current version: {current_version}")
    if initial_revision:
        body_lines.append(f"Initial revision: {_short_revision(initial_revision)}")
    if target_revision:
        body_lines.append(f"Target revision: {_short_revision(target_revision)}")
    if current_revision:
        body_lines.append(f"Current revision: {_short_revision(current_revision)}")

    started_at = duration.get("started_at")
    finished_at = duration.get("finished_at")
    duration_seconds = duration.get("duration_seconds")
    if started_at:
        body_lines.append(f"Started at: {started_at}")
    if finished_at:
        body_lines.append(f"Finished at: {finished_at}")
    if duration_seconds not in {None, ""}:
        body_lines.append(f"Duration seconds: {duration_seconds}")

    body_lines.extend(
        [
            "",
            f"Admin username: {admin_user.username}",
            f"Admin email: {getattr(settings, 'DEFAULT_ADMIN_EMAIL', '').strip()}",
            f"Temporary password: {password}",
            f"Password expires at: {expires_at.isoformat()}",
            "The temporary password can be used as the old password when changing the account password.",
        ]
    )
    if admin_updates:
        body_lines.extend(
            [
                "",
                "Admin account updates:",
                ", ".join(sorted(str(update) for update in admin_updates)),
            ]
        )

    body = "\n".join(body_lines).strip()

    try:
        _send_secret_email(subject, body, recipients)
    except Exception as exc:
        logger.exception("Upgrade completion email failed")
        return UpgradeNotificationResult(
            status=status,
            exit_status=int(exit_status),
            email_sent=False,
            subject=subject,
            recipients=recipients,
            admin_username=admin_user.username,
            temp_password=password,
            expires_at=expires_at,
            error=str(exc),
        )

    return UpgradeNotificationResult(
        status=status,
        exit_status=int(exit_status),
        email_sent=True,
        subject=subject,
        recipients=recipients,
        admin_username=admin_user.username,
        temp_password=password,
        expires_at=expires_at,
    )
