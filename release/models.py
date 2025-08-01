from django.db import models
from . import Package, Credentials, DEFAULT_PACKAGE


class PackageConfig(models.Model):
    """Store metadata and credentials for building a PyPI release."""

    name = models.CharField(max_length=100, default=DEFAULT_PACKAGE.name)
    description = models.CharField(max_length=255, default=DEFAULT_PACKAGE.description)
    author = models.CharField(max_length=100, default=DEFAULT_PACKAGE.author)
    email = models.EmailField(default=DEFAULT_PACKAGE.email)
    python_requires = models.CharField(max_length=20, default=DEFAULT_PACKAGE.python_requires)
    license = models.CharField(max_length=100, default=DEFAULT_PACKAGE.license)
    repository_url = models.URLField(default=DEFAULT_PACKAGE.repository_url)
    homepage_url = models.URLField(default=DEFAULT_PACKAGE.homepage_url)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)
    token = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Package Configuration"
        verbose_name_plural = "Package Configuration"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def to_package(self) -> Package:
        """Return a :class:`Package` instance for this configuration."""
        return Package(
            name=self.name,
            description=self.description,
            author=self.author,
            email=self.email,
            python_requires=self.python_requires,
            license=self.license,
            repository_url=self.repository_url,
            homepage_url=self.homepage_url,
        )

    def to_credentials(self) -> Credentials | None:
        """Return :class:`Credentials` if any credential fields are set."""
        if self.token:
            return Credentials(token=self.token)
        if self.username and self.password:
            return Credentials(username=self.username, password=self.password)
        return None

    def build(self, **kwargs) -> None:
        """Wrapper around :func:`release.utils.build` for convenience."""
        from . import utils

        utils.build(package=self.to_package(), creds=self.to_credentials(), **kwargs)
