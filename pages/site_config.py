"""Customizations for :mod:`django.contrib.sites`."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import DatabaseError, models
from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _


logger = logging.getLogger(__name__)


_FIELD_DEFINITIONS: tuple[tuple[str, models.Field], ...] = (
    (
        "managed",
        models.BooleanField(
            default=False,
            db_default=False,
            verbose_name=_("Managed by local NGINX"),
            help_text=_(
                "Include this site when staging the local NGINX configuration."
            ),
        ),
    ),
    (
        "require_https",
        models.BooleanField(
            default=False,
            db_default=False,
            verbose_name=_("Require HTTPS"),
            help_text=_(
                "Redirect HTTP traffic to HTTPS when the staged NGINX configuration is applied."
            ),
        ),
    ),
)


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

    SiteModel = apps.get_model("sites", "Site")
    data: list[dict[str, object]] = []
    seen_domains: set[str] = set()

    try:
        sites = list(
            SiteModel.objects.filter(managed=True)
            .only("domain", "require_https")
            .order_by("domain")
        )
    except DatabaseError:  # pragma: no cover - database not ready
        return

    for site in sites:
        domain = (site.domain or "").strip()
        if not domain:
            continue
        if domain.lower() in seen_domains:
            continue
        seen_domains.add(domain.lower())
        data.append({"domain": domain, "require_https": bool(site.require_https)})

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


def _install_fields() -> None:
    for name, field in _FIELD_DEFINITIONS:
        if hasattr(Site, name):
            continue
        Site.add_to_class(name, field.clone())


def ensure_site_fields() -> None:
    """Ensure the custom ``Site`` fields are installed."""

    _install_fields()


@receiver(post_save, sender=Site, dispatch_uid="pages_site_save_update_nginx")
def _site_saved(sender, **kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


@receiver(post_delete, sender=Site, dispatch_uid="pages_site_delete_update_nginx")
def _site_deleted(sender, **kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


def _run_post_migrate_update(**kwargs) -> None:  # pragma: no cover - signal wrapper
    update_local_nginx_scripts()


def ready() -> None:
    """Apply customizations and connect signal handlers."""

    ensure_site_fields()
    post_migrate.connect(
        _run_post_migrate_update,
        dispatch_uid="pages_site_post_migrate_update",
    )
