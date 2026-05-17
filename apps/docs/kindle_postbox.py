from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.docs import rendering
from apps.nodes.roles import node_is_control
from apps.sensors import usb_inventory

KINDLE_POSTBOX_NODE_FEATURE_SLUG = "kindle-postbox"
KINDLE_POSTBOX_USB_CLAIM = "kindle-postbox"
KINDLE_POSTBOX_BUNDLE_FILENAME = "arthexis-suite-documentation.txt"
KINDLE_POSTBOX_MANIFEST_FILENAME = "arthexis-suite-documentation.json"
KINDLE_DOCUMENTS_DIR_NAME = "documents"
DOCUMENT_EXTENSIONS = (
    rendering.MARKDOWN_FILE_EXTENSIONS
    | rendering.PLAINTEXT_FILE_EXTENSIONS
    | rendering.CSV_FILE_EXTENSIONS
    | {".rst"}
)


@dataclass(frozen=True)
class DocumentationBundle:
    output_path: Path
    manifest_path: Path
    generated_at: str
    document_count: int
    byte_count: int
    sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "manifest_path": str(self.manifest_path),
            "generated_at": self.generated_at,
            "document_count": self.document_count,
            "byte_count": self.byte_count,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class KindlePostboxTargetResult:
    root_path: Path
    documents_path: Path | None
    output_path: Path | None
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_path": str(self.root_path),
            "documents_path": str(self.documents_path) if self.documents_path else "",
            "output_path": str(self.output_path) if self.output_path else "",
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class KindlePostboxSyncResult:
    bundle: DocumentationBundle
    targets: tuple[KindlePostboxTargetResult, ...]
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle.as_dict(),
            "targets": [target.as_dict() for target in self.targets],
            "dry_run": self.dry_run,
        }


def kindle_postbox_available(*, node: object) -> bool:
    """Return whether this node can run Kindle postbox sync."""

    return bool(node is not None and node_is_control(node) and usb_inventory.has_usb_inventory_tools())


def default_output_dir() -> Path:
    return Path(
        getattr(
            settings,
            "KINDLE_POSTBOX_OUTPUT_DIR",
            Path(settings.BASE_DIR) / "work" / "docs" / "kindle-postbox",
        )
    )


def iter_suite_documentation_files(*, base_dir: Path | None = None) -> list[Path]:
    """Return documentation files that form the suite documentation bundle."""

    root = Path(base_dir or settings.BASE_DIR).resolve()
    candidates: list[Path] = []
    readme = root / "README.md"
    if readme.is_file():
        candidates.append(readme)

    for docs_root in (root / "docs", root / "apps" / "docs"):
        if not docs_root.exists():
            continue
        for path in sorted(docs_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(docs_root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
                continue
            candidates.append(path)

    seen: set[Path] = set()
    documents: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        documents.append(resolved)
    return documents


def build_suite_documentation_bundle(
    *,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
) -> DocumentationBundle:
    """Generate a single plain-text suite documentation bundle for Kindle readers."""

    root = Path(base_dir or settings.BASE_DIR).resolve()
    destination_dir = Path(output_dir or default_output_dir())
    destination_dir.mkdir(parents=True, exist_ok=True)
    output_path = destination_dir / KINDLE_POSTBOX_BUNDLE_FILENAME
    manifest_path = destination_dir / KINDLE_POSTBOX_MANIFEST_FILENAME
    generated_at = datetime.now(timezone.utc).isoformat()
    documents = iter_suite_documentation_files(base_dir=root)
    sources = tuple(path.relative_to(root).as_posix() for path in documents)

    lines = [
        "Arthexis Suite Documentation",
        f"Generated: {generated_at}",
        f"Source root: {root}",
        f"Documents: {len(documents)}",
        "",
        "Contents",
        "========",
    ]
    lines.extend(f"{index}. {source}" for index, source in enumerate(sources, start=1))
    lines.append("")

    for source, path in zip(sources, documents, strict=True):
        lines.extend(
            [
                "=" * 78,
                source,
                "=" * 78,
                "",
                rendering.read_document_text(path).strip(),
                "",
            ]
        )

    payload = "\n".join(lines).rstrip() + "\n"
    output_path.write_text(payload, encoding="utf-8")
    manifest = {
        "generated_at": generated_at,
        "document_count": len(documents),
        "byte_count": output_path.stat().st_size,
        "sources": list(sources),
        "output_path": str(output_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return DocumentationBundle(
        output_path=output_path,
        manifest_path=manifest_path,
        generated_at=generated_at,
        document_count=len(documents),
        byte_count=output_path.stat().st_size,
        sources=sources,
    )


def _resolve_documents_dir(root_path: Path) -> Path | None:
    if not root_path.is_dir():
        return None
    documents_dir = root_path / KINDLE_DOCUMENTS_DIR_NAME
    if documents_dir.is_dir():
        return documents_dir
    return root_path


def _copy_bundle_to_target(
    bundle: DocumentationBundle,
    root_path: Path,
    *,
    dry_run: bool,
) -> KindlePostboxTargetResult:
    documents_dir = _resolve_documents_dir(root_path)
    if documents_dir is None:
        return KindlePostboxTargetResult(
            root_path=root_path,
            documents_path=None,
            output_path=None,
            status="missing",
            error="Target path is not a directory.",
        )

    output_path = documents_dir / KINDLE_POSTBOX_BUNDLE_FILENAME
    if dry_run:
        return KindlePostboxTargetResult(
            root_path=root_path,
            documents_path=documents_dir,
            output_path=output_path,
            status="would-copy",
        )

    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        shutil.copyfile(bundle.output_path, temp_path)
        temp_path.replace(output_path)
    except OSError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        return KindlePostboxTargetResult(
            root_path=root_path,
            documents_path=documents_dir,
            output_path=output_path,
            status="failed",
            error=str(exc),
        )

    return KindlePostboxTargetResult(
        root_path=root_path,
        documents_path=documents_dir,
        output_path=output_path,
        status="copied",
    )


def sync_to_kindle_postboxes(
    *,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
    refresh_usb: bool = False,
    dry_run: bool = False,
    targets: list[str | Path] | None = None,
) -> KindlePostboxSyncResult:
    """Build suite documentation and copy it to Kindle postbox targets."""

    bundle = build_suite_documentation_bundle(output_dir=output_dir, base_dir=base_dir)
    raw_targets = (
        list(targets)
        if targets
        else usb_inventory.claimed_paths(
            KINDLE_POSTBOX_USB_CLAIM,
            refresh=refresh_usb,
        )
    )
    target_results = tuple(
        _copy_bundle_to_target(bundle, Path(path), dry_run=dry_run)
        for path in sorted({str(path) for path in raw_targets if str(path).strip()})
    )
    return KindlePostboxSyncResult(
        bundle=bundle,
        targets=target_results,
        dry_run=dry_run,
    )


__all__ = [
    "DOCUMENT_EXTENSIONS",
    "KINDLE_DOCUMENTS_DIR_NAME",
    "KINDLE_POSTBOX_BUNDLE_FILENAME",
    "KINDLE_POSTBOX_MANIFEST_FILENAME",
    "KINDLE_POSTBOX_NODE_FEATURE_SLUG",
    "KINDLE_POSTBOX_USB_CLAIM",
    "DocumentationBundle",
    "KindlePostboxSyncResult",
    "KindlePostboxTargetResult",
    "build_suite_documentation_bundle",
    "default_output_dir",
    "iter_suite_documentation_files",
    "kindle_postbox_available",
    "sync_to_kindle_postboxes",
]
