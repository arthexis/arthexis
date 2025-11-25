"""Nox sessions for local development automation."""

import nox

nox.options.sessions = ["tests", "lint"]


def install_requirements(session: nox.Session) -> None:
    """Install the project's Python dependencies."""

    session.install("-r", "requirements.txt")


@nox.session
def tests(session: nox.Session) -> None:
    """Run the pytest suite."""

    install_requirements(session)
    session.run("pytest", "tests")


@nox.session
def lint(session: nox.Session) -> None:
    """Check code formatting with Black."""

    session.install("black==25.11.0")
    session.run("black", "--check", "--diff", ".")
