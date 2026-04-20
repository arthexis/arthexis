from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class TestCommandPolicy:
    """Policy definition for an approved release test command wrapper."""

    __test__ = False

    value: str
    label: str
    prefix: tuple[str, ...]
    allowed_flags: frozenset[str]
    flags_with_values: frozenset[str] = frozenset()


MANAGED_DEFAULT_TEST_COMMAND = ""

TEST_COMMAND_POLICIES: tuple[TestCommandPolicy, ...] = (
    TestCommandPolicy(
        value=MANAGED_DEFAULT_TEST_COMMAND,
        label="Managed default (python manage.py test)",
        prefix=(),
        allowed_flags=frozenset(),
    ),
    TestCommandPolicy(
        value=".venv/bin/python manage.py test run --",
        label="Managed wrapper (.venv/bin/python manage.py test run --)",
        prefix=(".venv/bin/python", "manage.py", "test", "run", "--"),
        allowed_flags=frozenset(
            {
                "-k",
                "-q",
                "-v",
                "--exclude-tag",
                "--failfast",
                "--keepdb",
                "--parallel",
                "--shuffle",
                "--tag",
                "--timing",
                "--verbosity",
            }
        ),
        flags_with_values=frozenset(
            {
                "-k",
                "--exclude-tag",
                "--parallel",
                "--shuffle",
                "--tag",
                "--verbosity",
            }
        ),
    ),
)

TEST_COMMAND_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (policy.value, policy.label) for policy in TEST_COMMAND_POLICIES
)

_POLICY_BY_VALUE = {policy.value: policy for policy in TEST_COMMAND_POLICIES}


def normalize_test_command(command: str | None) -> list[str] | None:
    """Parse and validate a configured release test command.

    Returns ``None`` for the managed default command.
    """

    value = (command or "").strip()
    if value == MANAGED_DEFAULT_TEST_COMMAND:
        return None

    argv = shlex.split(value)
    policy = _policy_for_argv(argv)
    if policy is None:
        raise ValueError(
            "Use one of the approved release test command wrappers. "
            "Extend the release command tooling for new workflows."
        )

    _validate_flags(argv[len(policy.prefix) :], policy)
    return argv


def _policy_for_argv(argv: list[str]) -> TestCommandPolicy | None:
    candidates = [
        policy
        for policy in TEST_COMMAND_POLICIES
        if policy.prefix and list(policy.prefix) == argv[: len(policy.prefix)]
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: len(candidate.prefix))


def _validate_flags(args: Sequence[str], policy: TestCommandPolicy) -> None:
    idx = 0
    while idx < len(args):
        token = args[idx]
        if not token.startswith("-"):
            idx += 1
            continue

        if token not in policy.allowed_flags:
            raise ValueError(f"Unsupported flag for release test command: {token}")

        if token in policy.flags_with_values:
            idx += 1
            if idx >= len(args):
                raise ValueError(f"Flag requires a value: {token}")
        idx += 1
