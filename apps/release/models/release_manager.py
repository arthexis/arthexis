from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.base.models import EntityManager
from apps.sigils.fields import SigilShortAutoField
from apps.users.models import Profile

from ..release import Credentials, GitCredentials


class ReleaseManagerManager(EntityManager):
    def get_by_natural_key(self, owner, package=None):
        owner = owner or ""
        if owner.startswith("group:"):
            group_name = owner.split(":", 1)[1]
            return self.get(group__name=group_name)
        return self.get(user__username=owner)


class ReleaseManager(Profile):
    """Store credentials for publishing packages."""

    owner_required = True
    objects = ReleaseManagerManager()

    def natural_key(self):
        owner = self.owner_display()
        if self.group_id and owner:
            owner = f"group:{owner}"

        pkg_name = ""
        if self.pk:
            pkg = self.package_set.first()
            pkg_name = pkg.name if pkg else ""

        return (owner or "", pkg_name)

    profile_fields = (
        "pypi_username",
        "pypi_token",
        "github_token",
        "git_username",
        "git_password",
        "pypi_password",
        "pypi_url",
        "secondary_pypi_url",
    )
    pypi_username = SigilShortAutoField("PyPI username", max_length=100, blank=True)
    pypi_token = SigilShortAutoField("PyPI token", max_length=200, blank=True)
    github_token = SigilShortAutoField(
        max_length=200,
        blank=True,
        help_text=(
            "Personal access token for GitHub operations. "
            "Used before the GITHUB_TOKEN environment variable."
        ),
    )
    git_username = SigilShortAutoField(
        "Git username",
        max_length=100,
        blank=True,
        help_text="Username used for Git pushes (for example, your GitHub username).",
    )
    git_password = SigilShortAutoField(
        "Git password/token",
        max_length=200,
        blank=True,
        help_text=(
            "Password or personal access token for HTTPS Git pushes. "
            "Leave blank to use the GitHub token instead."
        ),
    )
    pypi_password = SigilShortAutoField("PyPI password", max_length=200, blank=True)
    pypi_url = SigilShortAutoField(
        "PyPI URL",
        max_length=200,
        blank=True,
        help_text=(
            "Link to the PyPI user profile (for example, https://pypi.org/user/username/). "
            "Use the account's user page, not a project-specific URL. "
            "This value is informational and not used for uploads."
        ),
    )
    secondary_pypi_url = SigilShortAutoField(
        "Secondary PyPI URL",
        max_length=200,
        blank=True,
        help_text=(
            "Optional secondary repository upload endpoint."
            " Leave blank to disable mirrored uploads."
        ),
    )

    class Meta:
        verbose_name = "Release Manager"
        verbose_name_plural = "Release Managers"
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="releasemanager_requires_owner",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    @property
    def name(self) -> str:  # pragma: no cover - simple proxy
        owner = self.owner_display()
        return owner or ""

    def to_credentials(self) -> Credentials | None:
        """Return credentials for this release manager."""
        if self.pypi_token:
            return Credentials(token=self.pypi_token)
        if self.pypi_username and self.pypi_password:
            return Credentials(username=self.pypi_username, password=self.pypi_password)
        return None

    def to_git_credentials(self) -> GitCredentials | None:
        """Return Git credentials for pushing tags."""

        username = (self.git_username or "").strip()
        password_source = self.git_password or self.github_token or ""
        password = password_source.strip()

        if password and not username and password_source == self.github_token:
            # GitHub personal access tokens require a username when used for
            # HTTPS pushes. Default to the recommended ``x-access-token`` so
            # release managers only need to provide their token.
            username = "x-access-token"

        if username and password:
            return GitCredentials(username=username, password=password)
        return None
