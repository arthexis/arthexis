"""Release-readiness simulation shared by local commands and CI."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import toml as tomllib  # type: ignore[no-redef]


DEFAULT_PACKAGE_NAME = "arthexis"


@dataclass
class SimulationStep:
    """One release-simulation step and its outcome."""

    name: str
    outcome: str
    detail: str = ""


@dataclass
class ReleaseSimulationResult:
    """Structured result for the release simulator."""

    package_name: str
    version: str
    ok: bool
    skipped: bool
    failed_step: str
    error: str
    summary_markdown: str
    blockers: list[str]
    steps: list[SimulationStep]


class ReleaseSimulationError(Exception):
    """Raised when a simulation step blocks the release."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


def run_release_simulation(
    *,
    root: Path,
    package_name: str = DEFAULT_PACKAGE_NAME,
    version_file: Path = Path("VERSION"),
    pyproject_path: Path = Path("pyproject.toml"),
    dist_dir: Path = Path("dist"),
    blockers: Sequence[str] = (),
    skip_pypi: bool = False,
    skip_build: bool = False,
    clean: bool = True,
    install_missing_tools: bool = False,
    pypi_timeout: float = 15.0,
    run_url: str = "",
) -> ReleaseSimulationResult:
    """Run the package-release simulation used by CI.

    The simulator intentionally stops before publishing so it can run both from
    GitHub Actions and from a local checkout without PyPI credentials.
    """

    root = root.resolve()
    steps: list[SimulationStep] = []
    blocker_list = [str(blocker) for blocker in blockers if str(blocker).strip()]
    if blocker_list:
        summary = _skipped_summary()
        steps.append(
            SimulationStep(
                name="evaluate_blockers",
                outcome="skipped",
                detail="Install/upgrade blockers are open.",
            )
        )
        return ReleaseSimulationResult(
            package_name=package_name,
            version="",
            ok=False,
            skipped=True,
            failed_step="",
            error="",
            summary_markdown=summary,
            blockers=blocker_list,
            steps=steps,
        )

    try:
        version = _validate_version_gate(
            root=root,
            version_file=version_file,
            pyproject_path=pyproject_path,
        )
        steps.append(
            SimulationStep(
                name="validate_version_gate",
                outcome="passed",
                detail=f"VERSION={version!r}",
            )
        )

        if skip_pypi:
            steps.append(
                SimulationStep(
                    name="preflight_pypi",
                    outcome="skipped",
                    detail="PyPI availability check skipped by caller.",
                )
            )
        else:
            _preflight_pypi(
                package_name=package_name,
                package_version=version,
                timeout=pypi_timeout,
            )
            steps.append(
                SimulationStep(
                    name="preflight_pypi",
                    outcome="passed",
                    detail=f"{package_name} {version} is not published on PyPI.",
                )
            )

        if skip_build:
            steps.extend(
                [
                    SimulationStep(
                        name="install_build_backend",
                        outcome="skipped",
                        detail="Build was skipped by caller.",
                    ),
                    SimulationStep(
                        name="build_package",
                        outcome="skipped",
                        detail="Build was skipped by caller.",
                    ),
                    SimulationStep(
                        name="validate_metadata",
                        outcome="skipped",
                        detail="Build was skipped by caller.",
                    ),
                ]
            )
        else:
            if install_missing_tools:
                _run_subprocess(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
                    cwd=root,
                    step="install_build_backend",
                )
                _run_subprocess(
                    [sys.executable, "-m", "pip", "install", "build", "twine"],
                    cwd=root,
                    step="install_build_backend",
                )
                steps.append(
                    SimulationStep(
                        name="install_build_backend",
                        outcome="passed",
                        detail="Ensured build and twine are installed.",
                    )
                )
            else:
                steps.append(
                    SimulationStep(
                        name="install_build_backend",
                        outcome="skipped",
                        detail="Assuming build and twine are already installed.",
                    )
                )

            if clean:
                _clean_artifacts(root=root, dist_dir=dist_dir)
            resolved_dist = _resolve_child_path(
                root,
                dist_dir,
                label="dist directory",
                step="build_package",
            )
            _run_subprocess(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--outdir",
                    str(resolved_dist),
                ],
                cwd=root,
                step="build_package",
            )
            steps.append(
                SimulationStep(
                    name="build_package",
                    outcome="passed",
                    detail=f"Built artifacts in {resolved_dist}.",
                )
            )

            dist_files = sorted(path for path in resolved_dist.glob("*") if path.is_file())
            if not dist_files:
                raise ReleaseSimulationError(
                    "validate_metadata",
                    f"No distribution artifacts found in {resolved_dist}.",
                )
            _run_subprocess(
                [sys.executable, "-m", "twine", "check", *(str(path) for path in dist_files)],
                cwd=root,
                step="validate_metadata",
            )
            steps.append(
                SimulationStep(
                    name="validate_metadata",
                    outcome="passed",
                    detail=f"Validated {len(dist_files)} artifact(s) with twine.",
                )
            )

        steps.append(
            SimulationStep(
                name="authorization_boundary",
                outcome="passed",
                detail="Publish intentionally skipped.",
            )
        )
        summary = _success_summary()
        return ReleaseSimulationResult(
            package_name=package_name,
            version=version,
            ok=True,
            skipped=False,
            failed_step="",
            error="",
            summary_markdown=summary,
            blockers=[],
            steps=steps,
        )
    except ReleaseSimulationError as exc:
        steps.append(SimulationStep(name=exc.step, outcome="failed", detail=exc.message))
        summary = _failure_summary(exc.step, run_url=run_url)
        return ReleaseSimulationResult(
            package_name=package_name,
            version=_safe_read_child_text(root, version_file),
            ok=False,
            skipped=False,
            failed_step=exc.step,
            error=exc.message,
            summary_markdown=summary,
            blockers=[],
            steps=steps,
        )


def parse_blockers_json(raw_value: str) -> list[str]:
    """Return blocker strings from a JSON list or an empty value."""

    value = raw_value.strip()
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ReleaseSimulationError("evaluate_blockers", f"Invalid blockers JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ReleaseSimulationError("evaluate_blockers", "Blockers JSON must be a list.")
    return [str(item) for item in payload if str(item).strip()]


def emit_result(
    result: ReleaseSimulationResult,
    *,
    github_output: Path | None = None,
    summary_file: Path | None = None,
    json_output: bool = False,
    stdout: Any = sys.stdout,
) -> None:
    """Write the simulator result to requested output surfaces."""

    if github_output is not None:
        write_github_output(result, github_output)
    if summary_file is not None:
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(result.summary_markdown + "\n", encoding="utf-8")
    if json_output:
        stdout.write(json.dumps(asdict(result), indent=2) + "\n")
    else:
        stdout.write(result.summary_markdown + "\n")


def write_github_output(result: ReleaseSimulationResult, path: Path) -> None:
    """Append GitHub Actions step outputs for the release simulator."""

    path.parent.mkdir(parents=True, exist_ok=True)
    delimiter = _github_output_delimiter(result.summary_markdown)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"summary_markdown<<{delimiter}\n")
        handle.write(result.summary_markdown.rstrip() + "\n")
        handle.write(f"{delimiter}\n")
        handle.write(f"simulated_ok={str(result.ok).lower()}\n")
        handle.write(f"simulated_skipped={str(result.skipped).lower()}\n")
        handle.write(f"failed_step={result.failed_step}\n")


def _github_output_delimiter(markdown: str) -> str:
    markdown_lines = set(markdown.splitlines())
    while True:
        delimiter = f"ghadelim_{secrets.token_hex(16)}"
        if delimiter not in markdown_lines:
            return delimiter


def _validate_version_gate(
    *,
    root: Path,
    version_file: Path,
    pyproject_path: Path,
) -> str:
    version_path = _resolve_child_path(root, version_file, label="version file")
    pyproject = _resolve_child_path(root, pyproject_path, label="pyproject file")
    if not version_path.exists():
        raise ReleaseSimulationError(
            "validate_version_gate",
            f"Version file not found: {version_path}",
        )
    if not pyproject.exists():
        raise ReleaseSimulationError(
            "validate_version_gate",
            f"pyproject file not found: {pyproject}",
        )

    expected_version = version_path.read_text(encoding="utf-8").strip()
    if not expected_version:
        raise ReleaseSimulationError(
            "validate_version_gate",
            f"Version file is empty: {version_path}",
        )

    try:
        pyproject_data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseSimulationError(
            "validate_version_gate",
            f"Failed to parse pyproject file: {exc}",
        ) from exc
    dynamic_version_files = (
        pyproject_data.get("tool", {})
        .get("setuptools", {})
        .get("dynamic", {})
        .get("version", {})
        .get("file", [])
    )
    if isinstance(dynamic_version_files, str):
        dynamic_version_files = [dynamic_version_files]

    if dynamic_version_files:
        dynamic_path = _resolve_child_path(
            root,
            Path(str(dynamic_version_files[0])),
            label="dynamic version file",
        )
        if not dynamic_path.exists():
            raise ReleaseSimulationError(
                "validate_version_gate",
                f"Dynamic version file not found: {dynamic_path}",
            )
        dynamic_version = dynamic_path.read_text(encoding="utf-8").strip()
        if dynamic_version != expected_version:
            raise ReleaseSimulationError(
                "validate_version_gate",
                "VERSION and setuptools dynamic version file differ. "
                f"VERSION={expected_version!r}; {dynamic_path}={dynamic_version!r}.",
            )
    return expected_version


def _preflight_pypi(
    *,
    package_name: str,
    package_version: str,
    timeout: float,
) -> None:
    pypi_url = f"https://pypi.org/pypi/{quote(package_name, safe='')}/json"
    try:
        with urlopen(pypi_url, timeout=timeout) as response:
            try:
                payload = json.load(response)
            except json.JSONDecodeError as exc:
                raise ReleaseSimulationError(
                    "preflight_pypi",
                    f"Received invalid JSON from PyPI: {exc}",
                ) from exc
    except HTTPError as exc:
        if exc.code == 404:
            payload = {"releases": {}}
        else:
            raise ReleaseSimulationError(
                "preflight_pypi",
                f"Unable to verify existing PyPI versions: HTTP {exc.code} from {pypi_url}.",
            ) from exc
    except URLError as exc:
        raise ReleaseSimulationError(
            "preflight_pypi",
            f"Network failure while checking PyPI for an existing release: {exc.reason}.",
        ) from exc

    releases = payload.get("releases")
    if releases is None:
        releases = {}
    elif not isinstance(releases, dict):
        raise ReleaseSimulationError(
            "preflight_pypi",
            f"Unexpected 'releases' payload type from PyPI: {type(releases).__name__}.",
        )
    if package_version in releases:
        raise ReleaseSimulationError(
            "preflight_pypi",
            f"{package_name} {package_version} is already available on PyPI.",
        )


def _run_subprocess(cmd: list[str], *, cwd: Path, step: str) -> None:
    try:
        completed = subprocess.run(cmd, cwd=cwd, check=False)
    except FileNotFoundError as exc:
        raise ReleaseSimulationError(step, f"Unable to run {cmd[0]!r}: {exc}") from exc
    if completed.returncode != 0:
        rendered = " ".join(cmd)
        raise ReleaseSimulationError(
            step,
            f"`{rendered}` exited with code {completed.returncode}.",
        )


def _clean_artifacts(*, root: Path, dist_dir: Path) -> None:
    for target in (
        _resolve_child_path(
            root,
            dist_dir,
            label="dist directory",
            step="build_package",
        ),
        root / "build",
    ):
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _resolve_child_path(
    root: Path,
    path: Path,
    *,
    label: str,
    step: str = "validate_version_gate",
) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleaseSimulationError(
            step,
            f"{label.capitalize()} escapes repository root: {resolved}",
        ) from exc
    return resolved


def _success_summary() -> str:
    return "\n".join(
        [
            "### Simulation result",
            "",
            "- OK Release simulation reached the authorization boundary successfully.",
            "- INFO Publish to PyPI was intentionally skipped because authorization is required.",
            "- OK Recommendation: release is ready if maintainers approve "
            "and trigger a real publish.",
        ]
    )


def _failure_summary(failed_step: str, *, run_url: str = "") -> str:
    lines = [
        "### Simulation result",
        "",
        "- FAIL Release simulation failed before reaching the authorization boundary.",
        f"- FAIL First failing step: `{failed_step}`.",
    ]
    if run_url:
        lines.append(f"- Inspect this workflow run for detailed logs: {run_url}")
    return "\n".join(lines)


def _skipped_summary() -> str:
    return "\n".join(
        [
            "### Simulation result",
            "",
            "- SKIP Release simulation was skipped because install/upgrade blockers are open.",
        ]
    )


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _safe_read_child_text(root: Path, path: Path) -> str:
    try:
        resolved_path = _resolve_child_path(root, path, label="version file")
    except ReleaseSimulationError:
        return ""
    return _safe_read_text(resolved_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulate release readiness without publishing.")
    parser.add_argument("--root", default=".", help="Repository root to simulate from.")
    parser.add_argument("--package-name", default=DEFAULT_PACKAGE_NAME)
    parser.add_argument("--version-file", default="VERSION")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--dist-dir", default="dist")
    parser.add_argument("--blockers-json", default="")
    parser.add_argument("--skip-pypi", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--no-clean", action="store_false", dest="clean", default=True)
    parser.add_argument("--install-missing-tools", action="store_true")
    parser.add_argument("--pypi-timeout", type=float, default=15.0)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--github-output")
    parser.add_argument("--summary-file")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        blockers = parse_blockers_json(args.blockers_json)
        result = run_release_simulation(
            root=Path(args.root),
            package_name=args.package_name,
            version_file=Path(args.version_file),
            pyproject_path=Path(args.pyproject),
            dist_dir=Path(args.dist_dir),
            blockers=blockers,
            skip_pypi=args.skip_pypi,
            skip_build=args.skip_build,
            clean=args.clean,
            install_missing_tools=args.install_missing_tools,
            pypi_timeout=args.pypi_timeout,
            run_url=args.run_url,
        )
    except ReleaseSimulationError as exc:
        result = ReleaseSimulationResult(
            package_name=args.package_name,
            version="",
            ok=False,
            skipped=False,
            failed_step=exc.step,
            error=exc.message,
            summary_markdown=_failure_summary(exc.step, run_url=args.run_url),
            blockers=[],
            steps=[SimulationStep(name=exc.step, outcome="failed", detail=exc.message)],
        )

    emit_result(
        result,
        github_output=Path(args.github_output) if args.github_output else None,
        summary_file=Path(args.summary_file) if args.summary_file else None,
        json_output=args.json_output,
    )
    return 0 if result.ok or result.skipped else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
