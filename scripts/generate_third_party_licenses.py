import json
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
DOC_PATH = ROOT / "docs" / "legal" / "THIRD_PARTY_LICENSES.md"
REQUIREMENTS_FILES = (
    ROOT / "requirements.txt",
    ROOT / "requirements-ci.txt",
)
LGPL3_URL = "https://www.gnu.org/licenses/lgpl-3.0.html"
LICENSE_OVERRIDES: dict[str, tuple[str, str]] = {
    "psycopg": ("LGPL-3.0-or-later", LGPL3_URL),
    "psycopg-binary": ("LGPL-3.0-or-later", LGPL3_URL),
}


def parse_dependency_spec(raw_spec: str) -> tuple[str, str] | None:
    """Return a package name and normalized display spec for dependency text."""
    dep = raw_spec.strip()
    if not dep or dep.startswith("#"):
        return None
    marker_split = dep.split(";", 1)
    spec = marker_split[0].strip()
    marker = marker_split[1].strip() if len(marker_split) == 2 else ""
    match = re.match(r"([A-Za-z0-9_.-]+)", spec)
    if not match:
        return None
    name = match.group(1)
    spec_display = spec + (f"; {marker}" if marker else "")
    return name, spec_display


def load_pyproject_dependencies() -> list[tuple[str, str]]:
    data = tomllib.loads(PYPROJECT.read_text())
    deps: list[str] = data.get("project", {}).get("dependencies", [])
    parsed: list[tuple[str, str]] = []
    for dep in deps:
        parsed_spec = parse_dependency_spec(dep)
        if parsed_spec:
            parsed.append(parsed_spec)
    return parsed


def load_generated_requirements() -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for req_path in REQUIREMENTS_FILES:
        for line in req_path.read_text().splitlines():
            parsed_spec = parse_dependency_spec(line)
            if parsed_spec:
                parsed.append(parsed_spec)
    return parsed


def resolve_transitive_dependencies() -> list[tuple[str, str]]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--quiet",
        "--report",
        "-",
    ]
    for req_path in REQUIREMENTS_FILES:
        command.extend(["-r", str(req_path)])

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("pip --report output did not contain JSON")
    report = json.loads(output[start : end + 1])

    resolved: list[tuple[str, str]] = []
    for item in report.get("install", []):
        metadata = item.get("metadata", {})
        name = metadata.get("name")
        version = metadata.get("version")
        if not name or not version:
            continue
        resolved.append((name, f"{name}=={version}"))
    return resolved


def fetch_license(name: str) -> tuple[str, str]:
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urlopen(url) as resp:  # noqa: S310 - trusted PyPI host
            payload = json.load(resp)
    except (HTTPError, URLError, json.JSONDecodeError):
        return "Unknown", f"https://pypi.org/project/{name}/"

    info = payload.get("info", {})
    license_name = " ".join((info.get("license") or "").split())
    if not license_name:
        classifiers = [
            c.split("::")[-1].strip()
            for c in info.get("classifiers", [])
            if c.startswith("License ::")
        ]
        if classifiers:
            license_name = "; ".join(classifiers)
        else:
            license_name = "Unknown"
    if not license_name:
        license_name = "Unknown"

    project_urls = info.get("project_urls") or {}
    license_url = (
        project_urls.get("License")
        or project_urls.get("Homepage")
        or f"https://pypi.org/project/{name}/"
    )
    if len(license_name) > 180:
        license_name = license_name[:177] + "..."

    override = LICENSE_OVERRIDES.get(name.lower())
    if override:
        license_name, license_url_override = override
        license_url = license_url_override or license_url

    license_name = license_name.replace("http://", "https://")
    license_url = license_url.replace("http://", "https://")

    return license_name, license_url


def build_inventory() -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    dependencies = {name.lower(): (name, spec) for name, spec in resolve_transitive_dependencies()}
    for name, spec in load_pyproject_dependencies():
        dependencies[name.lower()] = (name, spec)
    for name, spec in load_generated_requirements():
        dependencies[name.lower()] = (name, spec)
    for name, spec in dependencies.values():
        license_name, license_url = fetch_license(name)
        inventory.append(
            {
                "name": name,
                "spec": spec,
                "license": license_name,
                "license_url": license_url,
            }
        )
    return sorted(inventory, key=lambda item: item["name"].lower())


def render_markdown(inventory: list[dict[str, str]]) -> str:
    header = textwrap.dedent(
        """
        # Third-Party License Notices

        This project is distributed under the Arthexis Contribution Reciprocity License 1.0. In addition to the
        project's own license, the following third-party components are used at runtime. License
        information is collected from the Python Package Index (PyPI) and links point to the upstream
        license texts or project pages so downstream redistributors can comply with notice obligations.

        This inventory is generated from `pyproject.toml`, `requirements.txt`, and
        `requirements-ci.txt` so shipped runtime and CI pins stay aligned across releases.
        Regenerate with:

        ```bash
        python scripts/generate_requirements.py
        python scripts/generate_third_party_licenses.py
        ```

        ## Inventory

        | Package | Version / Marker | License | License text or project page |
        | --- | --- | --- | --- |
        """
    ).strip()

    rows = [
        f"| `{item['name']}` | `{item['spec']}` | {item['license']} | [link]({item['license_url']}) |"
        for item in inventory
    ]

    footer = textwrap.dedent(
        """

        License information is sourced from upstream package metadata. If a license field is listed as
        "Unknown", consult the linked project page for definitive terms or update this inventory once
        the upstream project clarifies its license.
        """
    ).strip()

    return "\n".join([header, *rows, footer]) + "\n"


def main() -> int:
    inventory = build_inventory()
    DOC_PATH.write_text(render_markdown(inventory))
    print(f"Wrote {len(inventory)} entries to {DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
