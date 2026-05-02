"""Windows/GWAY wrapper for Raspberry Pi image build and burn workflows."""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

TARGET_RPI4B = "rpi-4b"
DEFAULT_OUTPUT_DIR = "build/rpi-imager"
DEFAULT_GWAY_TARGET = "arthe@10.42.0.1"
DEFAULT_GWAY_SUITE = "/home/arthe/arthexis"
DEFAULT_GWAY_REMOTE_DIR = "/tmp/arthexis-imager"
DEFAULT_GWAY_PYTHON = ".venv/bin/python"
DEFAULT_RECOVERY_KEY_NAMES = (
    "id_ed25519.pub",
    "id_ecdsa.pub",
    "id_rsa.pub",
)


class ImagerScriptError(RuntimeError):
    """Raised when the helper cannot safely prepare a requested operation."""


class CommandRunner:
    """Thin subprocess wrapper used by the CLI and tests."""

    def run(self, command: Sequence[str], *, cwd: Path | None = None) -> None:
        subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def repo_root_from_script() -> Path:
    """Return the repository root for this helper script."""

    return Path(__file__).resolve().parents[1]


def venv_python(repo_root: Path) -> Path:
    """Return the repo-local Python interpreter expected by batch entrypoints."""

    if os.name == "nt":
        candidate = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = repo_root / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    if Path(sys.executable).exists():
        return Path(sys.executable)
    raise ImagerScriptError("Virtual environment not found. Run install.bat first.")


def has_option(args: Sequence[str], *names: str) -> bool:
    """Return True when args contains an option as --flag or --flag=value."""

    prefixes = tuple(f"{name}=" for name in names)
    return any(arg in names or arg.startswith(prefixes) for arg in args)


def find_default_recovery_key(
    *,
    env: os._Environ[str] | dict[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """Find the host public key used for default recovery SSH provisioning."""

    environment = env if env is not None else os.environ
    explicit = str(environment.get("GWAY_IMAGER_RECOVERY_KEY_FILE", "")).strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None

    ssh_home = (home or Path.home()) / ".ssh"
    for key_name in DEFAULT_RECOVERY_KEY_NAMES:
        candidate = ssh_home / key_name
        if candidate.exists():
            return candidate
    return None


def enrich_build_args(
    args: Sequence[str],
    *,
    repo_root: Path,
    recovery_key_file: Path | None = None,
) -> list[str]:
    """Add suite-source and recovery-key defaults unless the operator supplied them."""

    enriched = list(args)
    skip_customize = has_option(enriched, "--skip-customize")
    if (
        not skip_customize
        and not has_option(enriched, "--suite-source", "--no-bundle-suite")
    ):
        enriched.extend(["--suite-source", str(repo_root)])

    if skip_customize or has_option(enriched, "--skip-recovery-ssh"):
        return enriched
    if has_option(
        enriched,
        "--recovery-authorized-key-file",
        "--recovery-authorized-key",
    ):
        return enriched

    key_file = recovery_key_file or find_default_recovery_key()
    if key_file is None:
        raise ImagerScriptError(
            "Recovery SSH is required for customized images. "
            "Pass --recovery-authorized-key-file, set GWAY_IMAGER_RECOVERY_KEY_FILE, "
            "or explicitly pass --skip-recovery-ssh."
        )
    enriched.extend(["--recovery-authorized-key-file", str(key_file)])
    return enriched


def output_image_path(repo_root: Path, *, output_dir: str, name: str) -> Path:
    """Return the conventional output image path for an imager build."""

    path = Path(output_dir).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path / f"{name}-{TARGET_RPI4B}.img"


def safe_remote_filename(path: Path) -> str:
    """Return a conservative filename for uploading an image to GWAY."""

    return re.sub(r"[^A-Za-z0-9._-]", "_", path.name)


def run_local_imager(
    imager_args: Sequence[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """Run ``manage.py imager`` through the repo virtualenv."""

    runner.run(
        [str(venv_python(repo_root)), "manage.py", "imager", *imager_args],
        cwd=repo_root,
    )


def gway_remote_command(
    *,
    suite_path: str,
    python_path: str,
    imager_args: Sequence[str],
) -> str:
    """Build the remote shell command that invokes GWAY's suite writer."""

    return (
        f"cd {shlex.quote(suite_path)} && "
        f"{shlex.quote(python_path)} manage.py imager "
        + " ".join(shlex.quote(str(arg)) for arg in imager_args)
    ).strip()


def upload_image_to_gway(
    *,
    image_path: Path,
    ssh_target: str,
    remote_dir: str,
    runner: CommandRunner,
) -> str:
    """Copy a local image to GWAY and return its remote path."""

    if not image_path.exists():
        raise ImagerScriptError(f"Image file does not exist: {image_path}")
    remote_directory = remote_dir.rstrip("/") or DEFAULT_GWAY_REMOTE_DIR
    remote_path = posixpath.join(remote_directory, safe_remote_filename(image_path))
    runner.run(["ssh", ssh_target, f"mkdir -p {shlex.quote(remote_directory)}"])
    runner.run(["scp", str(image_path), f"{ssh_target}:{remote_path}"])
    return remote_path


def handle_build(args: argparse.Namespace, extra: list[str], *, repo_root: Path, runner: CommandRunner) -> None:
    """Build an image using the local suite checkout."""

    build_args = enrich_build_args(extra, repo_root=repo_root)
    run_local_imager(["build", *build_args], repo_root=repo_root, runner=runner)


def handle_devices_local(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """List local writer devices."""

    if extra:
        raise ImagerScriptError(f"Unexpected arguments for devices-local: {' '.join(extra)}")
    run_local_imager(["devices"], repo_root=repo_root, runner=runner)


def handle_burn_local(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """Burn an existing image/artifact through the local suite writer."""

    if extra:
        raise ImagerScriptError(f"Unexpected arguments for burn-local: {' '.join(extra)}")
    source_args = ["--artifact", args.artifact] if args.artifact else ["--image-path", args.image_path]
    write_args = ["write", *source_args, "--device", args.device]
    if args.yes:
        write_args.append("--yes")
    run_local_imager(write_args, repo_root=repo_root, runner=runner)


def handle_create_burn_local(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """Build locally and burn through the local suite writer."""

    build_args = [
        "--name",
        args.name,
        "--base-image-uri",
        args.base_image_uri,
        "--output-dir",
        args.output_dir,
        *extra,
    ]
    build_args = enrich_build_args(build_args, repo_root=repo_root)
    run_local_imager(["build", *build_args], repo_root=repo_root, runner=runner)
    image_path = output_image_path(repo_root, output_dir=args.output_dir, name=args.name)
    write_args = ["write", "--image-path", str(image_path), "--device", args.device]
    if args.yes:
        write_args.append("--yes")
    run_local_imager(write_args, repo_root=repo_root, runner=runner)


def handle_devices_gway(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """List candidate writer devices on GWAY."""

    if extra:
        raise ImagerScriptError(f"Unexpected arguments for devices-gway: {' '.join(extra)}")
    command = gway_remote_command(
        suite_path=args.gway_suite,
        python_path=args.remote_python,
        imager_args=["devices"],
    )
    runner.run(["ssh", args.gway, command])


def burn_gway_image(
    *,
    image_path: Path,
    device: str,
    yes: bool,
    gway: str,
    gway_suite: str,
    remote_dir: str,
    remote_python: str,
    runner: CommandRunner,
) -> None:
    """Upload an image to GWAY and invoke the remote suite writer."""

    remote_path = upload_image_to_gway(
        image_path=image_path,
        ssh_target=gway,
        remote_dir=remote_dir,
        runner=runner,
    )
    write_args = ["write", "--image-path", remote_path, "--device", device]
    if yes:
        write_args.append("--yes")
    command = gway_remote_command(
        suite_path=gway_suite,
        python_path=remote_python,
        imager_args=write_args,
    )
    runner.run(["ssh", gway, command])


def handle_burn_gway(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """Burn an existing local image through the GWAY suite writer."""

    if extra:
        raise ImagerScriptError(f"Unexpected arguments for burn-gway: {' '.join(extra)}")
    image_path = (
        Path(args.image_path).expanduser()
        if args.image_path
        else output_image_path(repo_root, output_dir=args.output_dir, name=args.artifact)
    )
    if not image_path.is_absolute():
        image_path = repo_root / image_path
    burn_gway_image(
        image_path=image_path,
        device=args.device,
        yes=args.yes,
        gway=args.gway,
        gway_suite=args.gway_suite,
        remote_dir=args.remote_dir,
        remote_python=args.remote_python,
        runner=runner,
    )


def handle_create_burn_gway(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """Build locally and burn through the GWAY suite writer."""

    build_args = [
        "--name",
        args.name,
        "--base-image-uri",
        args.base_image_uri,
        "--output-dir",
        args.output_dir,
        *extra,
    ]
    build_args = enrich_build_args(build_args, repo_root=repo_root)
    run_local_imager(["build", *build_args], repo_root=repo_root, runner=runner)
    image_path = output_image_path(repo_root, output_dir=args.output_dir, name=args.name)
    burn_gway_image(
        image_path=image_path,
        device=args.device,
        yes=args.yes,
        gway=args.gway,
        gway_suite=args.gway_suite,
        remote_dir=args.remote_dir,
        remote_python=args.remote_python,
        runner=runner,
    )


def handle_test_access(
    args: argparse.Namespace,
    extra: list[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> None:
    """Pass through to the local imager access test."""

    run_local_imager(["test-access", *extra], repo_root=repo_root, runner=runner)


def add_gway_options(parser: argparse.ArgumentParser) -> None:
    """Add shared GWAY SSH options to a subparser."""

    parser.add_argument(
        "--gway",
        default=os.environ.get("GWAY_IMAGER_SSH_TARGET", DEFAULT_GWAY_TARGET),
        help="SSH target for the GWAY bastion.",
    )
    parser.add_argument(
        "--gway-suite",
        default=os.environ.get("GWAY_IMAGER_SUITE", DEFAULT_GWAY_SUITE),
        help="Suite checkout path on GWAY.",
    )
    parser.add_argument(
        "--remote-dir",
        default=os.environ.get("GWAY_IMAGER_REMOTE_DIR", DEFAULT_GWAY_REMOTE_DIR),
        help="Temporary remote directory for uploaded images.",
    )
    parser.add_argument(
        "--remote-python",
        default=os.environ.get("GWAY_IMAGER_REMOTE_PYTHON", DEFAULT_GWAY_PYTHON),
        help="Python executable relative to the GWAY suite path, or an absolute path.",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Build and burn Arthexis Raspberry Pi images from Windows or through GWAY.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    build = subparsers.add_parser(
        "build",
        help="Build an image with local-suite and recovery-key defaults.",
    )
    build.set_defaults(handler=handle_build)

    devices_local = subparsers.add_parser("devices-local", help="List local writer devices.")
    devices_local.set_defaults(handler=handle_devices_local)

    burn_local = subparsers.add_parser(
        "burn-local",
        aliases=["write-local"],
        help="Burn an existing image/artifact through the local suite writer.",
    )
    local_source = burn_local.add_mutually_exclusive_group(required=True)
    local_source.add_argument("--artifact", default="", help="Registered artifact name to burn.")
    local_source.add_argument("--image-path", default="", help="Local image path to burn.")
    burn_local.add_argument("--device", required=True, help="Local writer device path.")
    burn_local.add_argument("--yes", action="store_true", help="Confirm destructive write.")
    burn_local.set_defaults(handler=handle_burn_local)

    create_burn_local = subparsers.add_parser(
        "create-burn-local",
        aliases=["create-write-local"],
        help="Build locally, then burn through the local suite writer.",
    )
    create_burn_local.add_argument("--name", required=True, help="Artifact name.")
    create_burn_local.add_argument("--base-image-uri", required=True, help="Base image URI/path.")
    create_burn_local.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Build output directory.")
    create_burn_local.add_argument("--device", required=True, help="Local writer device path.")
    create_burn_local.add_argument("--yes", action="store_true", help="Confirm destructive write.")
    create_burn_local.set_defaults(handler=handle_create_burn_local)

    devices_gway = subparsers.add_parser("devices-gway", help="List writer devices on GWAY.")
    add_gway_options(devices_gway)
    devices_gway.set_defaults(handler=handle_devices_gway)

    burn_gway = subparsers.add_parser(
        "burn-gway",
        aliases=["write-gway"],
        help="Upload and burn an existing image through GWAY.",
    )
    gway_source = burn_gway.add_mutually_exclusive_group(required=True)
    gway_source.add_argument("--image-path", default="", help="Local image path to upload and burn.")
    gway_source.add_argument("--artifact", default="", help="Artifact name under --output-dir to upload and burn.")
    burn_gway.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Artifact output directory.")
    burn_gway.add_argument("--device", required=True, help="Remote writer device, for example /dev/sdb.")
    burn_gway.add_argument("--yes", action="store_true", help="Confirm destructive remote write.")
    add_gway_options(burn_gway)
    burn_gway.set_defaults(handler=handle_burn_gway)

    create_burn_gway = subparsers.add_parser(
        "create-burn-gway",
        aliases=["create-write-gway"],
        help="Build locally, upload, then burn through GWAY.",
    )
    create_burn_gway.add_argument("--name", required=True, help="Artifact name.")
    create_burn_gway.add_argument("--base-image-uri", required=True, help="Base image URI/path.")
    create_burn_gway.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Build output directory.")
    create_burn_gway.add_argument("--device", required=True, help="Remote writer device, for example /dev/sdb.")
    create_burn_gway.add_argument("--yes", action="store_true", help="Confirm destructive remote write.")
    add_gway_options(create_burn_gway)
    create_burn_gway.set_defaults(handler=handle_create_burn_gway)

    access = subparsers.add_parser("test-access", help="Pass through to imager test-access.")
    access.set_defaults(handler=handle_test_access)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    repo_root: Path | None = None,
    runner: CommandRunner | None = None,
) -> int:
    """CLI entrypoint."""

    parser = build_arg_parser()
    parsed, extra = parser.parse_known_args(argv)
    root = repo_root or repo_root_from_script()
    command_runner = runner or CommandRunner()
    try:
        parsed.handler(parsed, extra, repo_root=root, runner=command_runner)
    except ImagerScriptError as exc:
        print(f"gway-imager: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
