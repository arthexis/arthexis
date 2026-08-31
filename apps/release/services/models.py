from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Package:
    """Metadata for building a distributable package."""

    name: str
    description: str
    author: str
    email: str
    python_requires: str
    license: str = ""
    repository_url: str = "https://github.com/arthexis/arthexis"
    homepage_url: str = "https://arthexis.com"
    packages: Sequence[str] | None = None
    version_path: Path | str | None = None
    dependencies_path: Path | str | None = None
    test_command: str | None = None
    generate_wheels: bool = False
    repositories: Sequence[RepositoryTarget] = ()

    def __post_init__(self) -> None:
        if self.packages is None:
            from .defaults import DEFAULT_PACKAGE_MODULES

            self.packages = tuple(DEFAULT_PACKAGE_MODULES)


@dataclass
class Credentials:
    """Credentials for uploading to PyPI."""

    token: str | None = None
    username: str | None = None
    password: str | None = None

    def has_auth(self) -> bool:
        return bool((self.token or "").strip()) or bool(
            (self.username or "").strip() and (self.password or "").strip()
        )

    def twine_args(self) -> list[str]:
        if self.token:
            return ["--username", "__token__", "--password", self.token]
        if self.username and self.password:
            return ["--username", self.username, "--password", self.password]
        raise ValueError("Missing PyPI credentials")

    def twine_env(self) -> dict[str, str]:
        if self.token:
            return {"TWINE_USERNAME": "__token__", "TWINE_PASSWORD": self.token}
        if self.username and self.password:
            return {"TWINE_USERNAME": self.username, "TWINE_PASSWORD": self.password}
        raise ValueError("Missing PyPI credentials")


@dataclass
class GitCredentials:
    """Credentials used for Git operations such as pushing tags."""

    username: str | None = None
    password: str | None = None

    def has_auth(self) -> bool:
        return bool((self.username or "").strip() and (self.password or "").strip())


@dataclass
class RepositoryTarget:
    """Configuration for uploading a distribution to a repository."""

    name: str
    repository_url: str | None = None
    credentials: Credentials | None = None
    verify_availability: bool = False
    extra_args: Sequence[str] = ()

    def build_command(self, files: Sequence[str]) -> list[str]:
        cmd = [sys.executable, "-m", "twine", "upload", *self.extra_args]
        if self.repository_url:
            cmd += ["--repository-url", self.repository_url]
        cmd += list(files)
        return cmd


class ReleaseError(Exception):
    pass
