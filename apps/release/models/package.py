from __future__ import annotations

from django.db import models

from apps.base.models import Entity, EntityManager

from ..release import DEFAULT_PACKAGE, Package as ReleasePackage
from .release_manager import ReleaseManager


class PackageManager(EntityManager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Package(Entity):
    """Package details shared across releases."""

    objects = PackageManager()

    def natural_key(self):
        return (self.name,)

    name = models.CharField(max_length=100, default=DEFAULT_PACKAGE.name, unique=True)
    description = models.CharField(max_length=255, default=DEFAULT_PACKAGE.description)
    author = models.CharField(max_length=100, default=DEFAULT_PACKAGE.author)
    email = models.EmailField(default=DEFAULT_PACKAGE.email)
    python_requires = models.CharField(
        max_length=20, default=DEFAULT_PACKAGE.python_requires
    )
    license = models.CharField(max_length=100, default=DEFAULT_PACKAGE.license)
    repository_url = models.URLField(default=DEFAULT_PACKAGE.repository_url)
    homepage_url = models.URLField(default=DEFAULT_PACKAGE.homepage_url)
    version_path = models.CharField(max_length=255, blank=True, default="")
    dependencies_path = models.CharField(max_length=255, blank=True, default="")
    test_command = models.TextField(blank=True, default="")
    generate_wheels = models.BooleanField(
        default=False,
        help_text="Build wheel distributions when creating releases",
    )
    release_manager = models.ForeignKey(
        ReleaseManager, on_delete=models.SET_NULL, null=True, blank=True
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Designates the active package for version comparisons",
    )

    class Meta:
        verbose_name = "Package"
        verbose_name_plural = "Packages"
        constraints = [
            models.UniqueConstraint(
                fields=("is_active",),
                condition=models.Q(is_active=True),
                name="unique_active_package",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def save(self, *args, **kwargs):
        if self.is_active:
            type(self).objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` instance from package data."""
        repositories = [
            repo.to_target() for repo in self.package_repositories.all().order_by("pk")
        ]
        return ReleasePackage(
            name=self.name,
            description=self.description,
            author=self.author,
            email=self.email,
            python_requires=self.python_requires,
            license=self.license,
            repository_url=self.repository_url,
            homepage_url=self.homepage_url,
            version_path=self.version_path or None,
            dependencies_path=self.dependencies_path or None,
            test_command=self.test_command or None,
            generate_wheels=self.generate_wheels,
            repositories=repositories,
        )
