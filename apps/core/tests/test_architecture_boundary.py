"""Temporary shrink-only guardrails for the staged apps.core refactor."""

from __future__ import annotations

import ast
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1]

# These are legacy ceilings, not desired end-state APIs. Refactor steps may remove
# entries without updating this set. New core-owned model surface must not be added.
LEGACY_CORE_MODEL_EXPORTS = {
    "AdminNotice",
    "EmailArtifact",
    "EmailTransaction",
    "EmailTransactionAttachment",
    "InviteLead",
    "LeadBase",
    "OwnedObjectLink",
    "Ownable",
    "UsageEvent",
    "get_ownable_models",
    "get_owned_objects_for_group",
    "get_owned_objects_for_user",
}

LEGACY_CORE_MODEL_MODULES = {
    "__init__.py",
    "admin_notice.py",
    "email.py",
    "invite_lead.py",
    "lead_base.py",
    "ownable.py",
    "security_group.py",
    "usage_event.py",
}

LEGACY_CORE_REQUIRED_APPS = {
    "apps.discovery",
    "apps.emails",
}


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        return ast.literal_eval(node.value)
    raise AssertionError(f"{path} does not define a literal {name} assignment")


def test_core_model_exports_can_only_shrink() -> None:
    exports = set(_literal_assignment(CORE_DIR / "models" / "__init__.py", "__all__"))

    unexpected = exports - LEGACY_CORE_MODEL_EXPORTS
    assert not unexpected, (
        "apps.core must not gain new model exports during the boundary refactor; "
        f"move the new concept to its owning app instead: {sorted(unexpected)}"
    )


def test_core_model_modules_can_only_shrink() -> None:
    modules = {path.name for path in (CORE_DIR / "models").glob("*.py")}

    unexpected = modules - LEGACY_CORE_MODEL_MODULES
    assert not unexpected, (
        "apps.core must not gain new model modules during the boundary refactor; "
        f"move the new concept to its owning app instead: {sorted(unexpected)}"
    )


def test_core_manifest_dependencies_can_only_shrink() -> None:
    required_apps = set(_literal_assignment(CORE_DIR / "manifest.py", "REQUIRES_APPS"))

    unexpected = required_apps - LEGACY_CORE_REQUIRED_APPS
    assert not unexpected, (
        "apps.core must not gain new manifest dependencies during the boundary refactor; "
        f"new dependencies belong in the owning domain app: {sorted(unexpected)}"
    )
