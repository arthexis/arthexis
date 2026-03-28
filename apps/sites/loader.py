"""Utilities for hydrating pages badge seed data after migrations."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management import call_command

from .models import SiteBadge

ADMIN_BADGE_FIXTURE_GLOB = "admin_badges__*.json"


def _admin_badge_fixture_paths() -> list[Path]:
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    return sorted(fixtures_dir.glob(ADMIN_BADGE_FIXTURE_GLOB))


def load_admin_badge_seed_data(sender=None, **kwargs) -> None:
    """Hydrate bundled admin badge fixtures during migrate/install flows."""

    del sender
    using = kwargs.get("using") or "default"
    fixture_paths = _admin_badge_fixture_paths()
    if not fixture_paths:
        return
    call_command(
        "loaddata",
        *(str(path.relative_to(Path(settings.BASE_DIR))) for path in fixture_paths),
        database=using,
        verbosity=0,
    )


def ensure_site_badges_exist(sender=None, **kwargs) -> None:
    """Ensure every site has a SiteBadge row after migrations."""

    del sender
    using = kwargs.get("using") or "default"
    existing_site_ids = set(SiteBadge.objects.using(using).values_list("site_id", flat=True))
    missing_site_ids = [
        site_id
        for site_id in Site.objects.using(using).values_list("pk", flat=True)
        if site_id not in existing_site_ids
    ]
    if not missing_site_ids:
        return
    SiteBadge.objects.using(using).bulk_create(
        [SiteBadge(site_id=site_id, is_seed_data=True) for site_id in missing_site_ids]
    )
