from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

METADATA_SCHEMA_VERSION = 1
METADATA_RELATIVE_PATH = Path(".gway") / "arthexis.json"
PROJECT_NAME = "arthexis"


class LifecycleMode(str, Enum):
    """How an Arthexis instance participates in the lifecycle."""

    UNMANAGED = "unmanaged"
    MANAGED = "managed"


@dataclass(frozen=True)
class OwnershipLayout:
    """Named paths used by the managed Arthexis lifecycle contract."""

    root: Path
    checkout: Path
    environment: Path
    metadata: Path
    state: Path
    data: Path
    logs: Path
    cache: Path
    run: Path

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        *,
        checkout_name: str = "app",
        environment_name: str = ".venv",
    ) -> OwnershipLayout:
        selected_root = Path(root).expanduser()
        state = selected_root / "var"
        return cls(
            root=selected_root,
            checkout=selected_root / checkout_name,
            environment=selected_root / environment_name,
            metadata=selected_root / METADATA_RELATIVE_PATH,
            state=state,
            data=state / "lib",
            logs=state / "log",
            cache=state / "cache",
            run=state / "run",
        )

    @property
    def managed_resources(self) -> tuple[Path, ...]:
        """Resources whose lifecycle is owned by GWAY-managed Arthexis."""
        return (
            self.checkout,
            self.environment,
            self.metadata,
            self.logs,
            self.cache,
            self.run,
        )

    @property
    def persistent_resources(self) -> tuple[Path, ...]:
        """Instance state that an ordinary managed uninstall must preserve."""
        return (self.data,)


@dataclass(frozen=True)
class ManagedInstallation:
    """Conservative classification of one candidate managed installation."""

    mode: LifecycleMode
    layout: OwnershipLayout
    valid: bool
    installation_id: str | None = None
    metadata_version: int | None = None
    problems: tuple[str, ...] = ()


class OwnershipError(RuntimeError):
    """Raised when managed ownership metadata conflicts with the requested layout."""


def _expected_metadata(layout: OwnershipLayout, installation_id: str) -> dict[str, object]:
    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "project": PROJECT_NAME,
        "installation_id": installation_id,
        "root": str(layout.root.resolve()),
        "checkout": str(layout.checkout.resolve()),
    }


def classify_managed_installation(
    root: str | Path,
    *,
    checkout_name: str = "app",
    environment_name: str = ".venv",
) -> ManagedInstallation:
    """Classify a path without claiming ordinary source checkouts as managed."""
    current = OwnershipLayout.from_root(
        root,
        checkout_name=checkout_name,
        environment_name=environment_name,
    )
    if not current.metadata.exists():
        return ManagedInstallation(
            mode=LifecycleMode.UNMANAGED,
            layout=current,
            valid=True,
        )

    problems: list[str] = []
    try:
        payload = json.loads(current.metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return ManagedInstallation(
            mode=LifecycleMode.UNMANAGED,
            layout=current,
            valid=False,
            problems=(f"invalid ownership metadata: {exc}",),
        )

    if not isinstance(payload, dict):
        problems.append("ownership metadata must be a JSON object")
        payload = {}

    version = payload.get("schema_version")
    if version != METADATA_SCHEMA_VERSION:
        problems.append(
            f"unsupported ownership metadata version: {version!r}"
        )

    if payload.get("project") != PROJECT_NAME:
        problems.append("ownership metadata belongs to another project")

    installation_id = payload.get("installation_id")
    if not isinstance(installation_id, str) or not installation_id.strip():
        problems.append("ownership metadata has no installation id")
        installation_id = None

    expected_root = str(current.root.resolve())
    expected_checkout = str(current.checkout.resolve())
    if payload.get("root") != expected_root:
        problems.append("ownership metadata root does not match this installation")
    if payload.get("checkout") != expected_checkout:
        problems.append("ownership metadata checkout does not match this installation")
    if not current.checkout.is_dir():
        problems.append("managed checkout is missing")

    if problems:
        return ManagedInstallation(
            mode=LifecycleMode.UNMANAGED,
            layout=current,
            valid=False,
            installation_id=installation_id,
            metadata_version=version if isinstance(version, int) else None,
            problems=tuple(problems),
        )

    return ManagedInstallation(
        mode=LifecycleMode.MANAGED,
        layout=current,
        valid=True,
        installation_id=installation_id,
        metadata_version=METADATA_SCHEMA_VERSION,
    )


def record_managed_installation(
    root: str | Path,
    *,
    checkout_name: str = "app",
    environment_name: str = ".venv",
) -> ManagedInstallation:
    """Record managed ownership, preserving an existing installation identity."""
    current = OwnershipLayout.from_root(
        root,
        checkout_name=checkout_name,
        environment_name=environment_name,
    )
    if not current.checkout.is_dir():
        raise OwnershipError(f"managed checkout is missing: {current.checkout}")

    existing = classify_managed_installation(
        current.root,
        checkout_name=checkout_name,
        environment_name=environment_name,
    )
    if current.metadata.exists() and not existing.valid:
        raise OwnershipError("; ".join(existing.problems))

    installation_id = existing.installation_id or str(uuid.uuid4())
    current.metadata.parent.mkdir(parents=True, exist_ok=True)
    current.metadata.write_text(
        json.dumps(
            _expected_metadata(current, installation_id),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return classify_managed_installation(
        current.root,
        checkout_name=checkout_name,
        environment_name=environment_name,
    )
