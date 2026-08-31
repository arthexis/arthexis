from __future__ import annotations

_PackageReleaseModel = None


def _get_package_release_model():
    """Return the :class:`release.models.PackageRelease` model when available."""

    global _PackageReleaseModel

    if _PackageReleaseModel is not None:
        return _PackageReleaseModel

    try:
        from apps.release.models import PackageRelease  # noqa: WPS433 - runtime import
    except Exception:  # pragma: no cover - app registry not ready
        return None

    _PackageReleaseModel = PackageRelease
    return PackageRelease
