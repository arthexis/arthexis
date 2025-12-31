from __future__ import annotations

import pytest


def feature_marker(slug: str, package: str | None = None):
    """Return a pytest marker tying the test to a release Feature."""

    return pytest.mark.feature({"slug": slug, "package": package})


def feature_test(slug: str, package: str | None = None):
    """Decorator to annotate a test with a target feature."""

    def decorator(func):
        return feature_marker(slug, package)(func)

    return decorator


class FeatureTestMixin:
    """Mixin that automatically marks subclasses with a feature reference."""

    feature_slug: str
    feature_package: str | None = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        slug = getattr(cls, "feature_slug", None)
        if slug:
            pytestmark = getattr(cls, "pytestmark", [])
            if not isinstance(pytestmark, list):
                pytestmark = [pytestmark]
            pytestmark.append(feature_marker(slug, getattr(cls, "feature_package", None)))
            cls.pytestmark = pytestmark
