from django.db import models
from django.core.management import call_command
from io import StringIO
from pathlib import Path
import json
from . import Package, Credentials, DEFAULT_PACKAGE


class Todo(models.Model):
    """A simple task item extracted from code or created by users."""

    text = models.CharField(max_length=255)
    completed = models.BooleanField(default=False)
    file_path = models.CharField(max_length=255, blank=True)
    line_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("text", "file_path", "line_number")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.text


class TestLog(models.Model):
    """Store output of test runs for release builds."""

    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=7, choices=[("success", "Success"), ("failure", "Failure")]
    )
    output = models.TextField()

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.created:%Y-%m-%d %H:%M:%S} - {self.status}"


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


class SeedData(models.Model):
    """Snapshot of data marked as seed data."""

    created = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=list)
    auto_install = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Seed Datum"
        verbose_name_plural = "Seed Data"

    @property
    def path(self) -> Path:
        return Path(__file__).resolve().parent / "seed_data" / f"{self.pk}.json"

    @classmethod
    def create_snapshot(cls, auto_install: bool = False) -> "SeedData":
        """Create a snapshot of current seed data."""
        obj = cls.objects.create(data=[], auto_install=auto_install)
        obj.path.parent.mkdir(parents=True, exist_ok=True)
        call_command("dumpseeddata", str(obj.path))
        obj.data = json.loads(obj.path.read_text())
        obj.save(update_fields=["data"])
        update_seeddata_fixture()
        return obj

    def install(self) -> None:
        if self.path.exists():
            call_command("loaddata", str(self.path))

    def delete(self, *args, **kwargs) -> None:  # pragma: no cover - simple
        path = self.path
        super().delete(*args, **kwargs)
        if path.exists():
            path.unlink()
        update_seeddata_fixture()


FIXTURE_FILE = Path(__file__).resolve().parent / "fixtures" / "seed_data.json"


def update_seeddata_fixture() -> None:
    """Write all SeedData objects to the fixture file."""
    FIXTURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = StringIO()
    call_command("dumpdata", "release.SeedData", "--indent", 2, stdout=out)
    FIXTURE_FILE.write_text(out.getvalue(), encoding="utf-8")


def load_seeddata_fixture(**_kwargs) -> None:
    """Load SeedData objects and auto-install marked snapshots."""
    if FIXTURE_FILE.exists() and FIXTURE_FILE.read_text().strip() not in ("", "[]"):
        call_command("loaddata", str(FIXTURE_FILE), verbosity=0)
        for seed in SeedData.objects.filter(auto_install=True):
            if seed.path.exists():
                call_command("loaddata", str(seed.path), verbosity=0)
