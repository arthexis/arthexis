from dataclasses import dataclass
from typing import Optional


@dataclass
class Package:
    """Metadata for building a distributable package."""

    name: str
    description: str
    author: str
    email: str
    python_requires: str
    license: str
    repository_url: str = "https://github.com/arthexis/arthexis"
    homepage_url: str = "https://arthexis.com"


@dataclass
class Credentials:
    """Credentials for uploading to PyPI."""

    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    def twine_args(self) -> list[str]:
        """Return command line arguments for Twine."""
        if self.token:
            return ["--username", "__token__", "--password", self.token]
        if self.username and self.password:
            return ["--username", self.username, "--password", self.password]
        raise ValueError("Missing PyPI credentials")


DEFAULT_PACKAGE = Package(
    name="arthexis",
    description="Django-based MESH system",
    author="Rafael J. Guill\u00e9n-Osorio",
    email="tecnologia@gelectriic.com",
    python_requires=">=3.10",
    license="MIT",
)

__all__ = ["Package", "Credentials", "DEFAULT_PACKAGE"]
