from __future__ import annotations

from .models import Package, RepositoryTarget


def _get_default_packages() -> list[str]:
    from setuptools import find_packages

    return find_packages(include=["apps.*", "config"])


DEFAULT_PACKAGE_MODULES = _get_default_packages()


DEFAULT_PACKAGE = Package(
    name="arthexis",
    description="Energy & Power Infrastructure",
    author="Rafael J. Guillén-Osorio",
    email="tecnologia@gelectriic.com",
    python_requires=">=3.10",
    license="GPL-3.0-only",
    packages=tuple(DEFAULT_PACKAGE_MODULES),
    repositories=(RepositoryTarget(name="PyPI", verify_availability=True),),
)
