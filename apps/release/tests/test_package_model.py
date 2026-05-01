"""Model-level validation tests for release package configuration."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.release.models import Package, PackageRelease


@pytest.mark.django_db
def test_package_full_clean_rejects_unapproved_test_command() -> None:
    package = Package(name="release-model-test", test_command="python manage.py test")

    with pytest.raises(ValidationError) as exc_info:
        package.full_clean()

    assert "test_command" in exc_info.value.message_dict


@pytest.mark.parametrize(
    ("repo_url", "expected"),
    (
        ("https://github.com/arthexis/arthexis", "arthexis/arthexis"),
        ("https://github.com:443/arthexis/arthexis.git", "arthexis/arthexis"),
        ("git@github.com:arthexis/arthexis.git", "arthexis/arthexis"),
        ("https://evilgithub.com/arthexis/arthexis", None),
        ("git@evilgithub.com:arthexis/arthexis.git", None),
    ),
)
def test_package_release_github_repository_path_validates_hosts(
    repo_url: str,
    expected: str | None,
) -> None:
    assert PackageRelease._github_repository_path_from_url(repo_url) == expected
