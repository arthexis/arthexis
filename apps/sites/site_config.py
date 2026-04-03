"""Customizations for :mod:`django.contrib.sites`."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import DatabaseError
from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver

from .models import SiteProfile


logger = logging.getLogger(__name__)


def _sites_config_path() -> Path:
    return Path(settings.BASE_DIR) / "scripts" / "generated" / "nginx-sites.json"


def _ensure_directories(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - filesystem errors
        logger.warning("Unable to create directory for %s: %s", path, exc)
        return False
    return True


def update_local_nginx_scripts() -> None:
    """Serialize managed site configuration for the network setup script."""

    data: list[dict[str, object]] = []
    seen_domains: set[str] = set()

    try:
        profiles = list(
            SiteProfile.objects.filter(managed=True)
            .select_related("site")
            .only("site__domain", "require_https")
            .order_by("site__domain")
        )
    except DatabaseError:  # pragma: no cover - database not ready
        return

    for profile in profiles:
        domain = (profile.site.domain or "").strip()
        if not domain:
            continue
        if domain.lower() in seen_domains:
            continue
        seen_domains.add(domain.lower())
        data.append({"domain": domain, "require_https": bool(profile.require_https)})

    output_path = _sites_config_path()
    if not _ensure_directories(output_path):
        return

    if data:
        try:
            output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - filesystem errors
            logger.warning("Failed to write managed site configuration: %s", exc)
    else:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover - filesystem errors
            logger.warning("Failed to remove managed site configuration: %s", exc)


@receiver(post_save, sender=Site, dispatch_uid="pages_site_save_update_nginx")
def _site_saved(sender, **kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


@receiver(post_delete, sender=Site, dispatch_uid="pages_site_delete_update_nginx")
def _site_deleted(sender, **kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


@receiver(
    post_save, sender=SiteProfile, dispatch_uid="pages_site_profile_save_update_nginx"
)
def _site_profile_saved(sender, **kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


@receiver(
    post_delete,
    sender=SiteProfile,
    dispatch_uid="pages_site_profile_delete_update_nginx",
)
def _site_profile_deleted(
    sender, **kwargs
) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


def _run_post_migrate_update(**kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


def ready() -> None:
    """Connect signal handlers."""

    post_migrate.connect(
        _run_post_migrate_update,
        dispatch_uid="pages_site_post_migrate_update",
    )
