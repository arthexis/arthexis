"""Download and prepare official OCPP JSON schemas for heavy conformance tests."""

from __future__ import annotations

import argparse
import json
import zipfile
from io import BytesIO
from pathlib import Path

from scripts.ocpp_spec_refresh import (
    _get,
    _load_manifest,
    discover_download_url,
)


def _schema_kind(path: str) -> tuple[str, str] | None:
    stem = Path(path).stem
    for suffix, kind in (("Request", "request"), ("Response", "response")):
        if stem.endswith(suffix):
            action = stem[: -len(suffix)]
            if action:
                return action, kind
    return None


def prepare_manifest(
    manifest_path: Path,
    *,
    output_root: Path,
) -> dict[str, object]:
    manifest = _load_manifest(manifest_path)
    source_page = str(manifest["source_page"])
    label = str(manifest["download_label"])
    listing = _get(source_page).decode("utf-8")
    download_url = discover_download_url(listing, label=label, base_url=source_page)
    archive = _get(download_url)

    version_root = output_root / manifest_path.stem
    version_root.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, str]] = {}

    with zipfile.ZipFile(BytesIO(archive)) as bundle:
        for member in bundle.infolist():
            if member.is_dir() or not member.filename.lower().endswith(".json"):
                continue
            identified = _schema_kind(member.filename)
            if identified is None:
                continue
            action, kind = identified
            target = version_root / f"{action}{kind.title()}.json"
            payload = bundle.read(member)
            try:
                json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if target.exists() and target.read_bytes() != payload:
                raise RuntimeError(
                    f"Conflicting official schemas for {action} {kind} in {label}"
                )
            target.write_bytes(payload)
            index.setdefault(action, {})[kind] = str(target.relative_to(output_root))

    official_actions = set(manifest["directions"]["charge_point_to_csms"]) | set(
        manifest["directions"]["csms_to_charge_point"]
    )
    missing_pairs = sorted(
        action
        for action in official_actions
        if set(index.get(action, {})) != {"request", "response"}
    )
    if missing_pairs:
        raise RuntimeError(
            f"Official package is missing request/response schemas: {missing_pairs!r}"
        )

    result = {
        "protocol": manifest["protocol"],
        "publication": manifest["publication"],
        "actions": {action: index[action] for action in sorted(official_actions)},
    }
    (version_root / "index.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path("tests/ocpp/spec"),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    for filename in ("ocpp16.json", "ocpp201.json"):
        prepare_manifest(
            args.manifest_dir / filename,
            output_root=args.output_root,
        )
    print(f"schema_root={args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
