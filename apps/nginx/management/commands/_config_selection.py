from __future__ import annotations

from typing import Iterable

from apps.nginx.models import SiteConfiguration


def parse_ids(ids_value: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in (ids_value or "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            pk_value = int(value)
        except ValueError:
            continue
        if pk_value in seen:
            continue
        seen.add(pk_value)
        ids.append(pk_value)
    return ids


def get_configurations(ids_value: str, *, select_all: bool) -> Iterable[SiteConfiguration]:
    if select_all:
        return SiteConfiguration.objects.all()

    ids = parse_ids(ids_value)
    if not ids:
        return SiteConfiguration.objects.none()

    return SiteConfiguration.objects.filter(pk__in=ids)
