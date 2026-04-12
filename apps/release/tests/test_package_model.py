"""Model-level validation tests for release package configuration."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.release.models import Package


@pytest.mark.django_db
def test_package_full_clean_rejects_unapproved_test_command() -> None:
    package = Package(name="release-model-test", test_command="python manage.py test")

    with pytest.raises(ValidationError) as exc_info:
        package.full_clean()

    assert "test_command" in exc_info.value.message_dict
