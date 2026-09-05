#!/usr/bin/env python3
"""Build GWAY images with deterministic parent recovery-key selection."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import subprocess
import sys
from pathlib import Path

KEY_FILE_ENV = "IMAGER_GWAY_RECOVERY_AUTHORIZED_KEY_FILE"
DEFAULT_PUBLIC_KEY_FILES = (
    "id_ed25519.pub",
    "id_ecdsa.pub",
    "id_rsa.pub",
)
EXPLICIT_RECOVERY_OPTIONS = (
    "--recovery-authorized-key-file",
    "--recovery-authorized-key",
    "--skip-recovery-ssh",
)


def public_key_fingerprint(public_key: str) -> str:
    """Return an OpenSSH-style SHA256 fingerprint without exposing key material."""

    fields = public_key.strip().split()
    if len(fields) < 2:
        raise ValueError("public key is missing its encoded key body")
    try:
        key_bytes = base64.b64decode(fields[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("public key body is not valid base64") from exc
    digest = base64.b64encode(hashlib.sha256(key_bytes).digest()).decode("ascii")
    return f"SHA256:{digest.rstrip('=')}"


def resolve_parent_public_key(*, home: Path | None = None) -> Path:
    """Resolve the public key authorized for recovery SSH on a new GWAY."""

    configured = os.environ.get(KEY_FILE_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{KEY_FILE_ENV} does not name a file: {path}")
        return path.resolve()

    ssh_dir = (home or Path.home()).expanduser() / ".ssh"
    for filename in DEFAULT_PUBLIC_KEY_FILES:
        candidate = ssh_dir / filename
        if candidate.is_file():
            return candidate.resolve()
    candidates = ", ".join(str(ssh_dir / name) for name in DEFAULT_PUBLIC_KEY_FILES)
    raise FileNotFoundError(
        "No GWAY recovery public key found. Set "
        f"{KEY_FILE_ENV} or create one of: {candidates}"
    )


def _has_explicit_recovery_option(arguments: list[str]) -> bool:
    """Return whether the caller already chose the recovery SSH behavior."""

    for argument in arguments:
        if any(
            argument == option or argument.startswith(f"{option}=")
            for option in EXPLICIT_RECOVERY_OPTIONS
        ):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """Run the standard imager GWAY burn workflow with a deterministic key."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parent.parent
    command = [sys.executable, str(repo_root / "manage.py"), "imager", "gway-burn"]

    if not _has_explicit_recovery_option(arguments):
        try:
            key_path = resolve_parent_public_key()
            key_text = key_path.read_text(encoding="utf-8").strip()
            fingerprint = public_key_fingerprint(key_text)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"gway-burn: {exc}", file=sys.stderr)
            return 2
        print(f"recovery_ssh_key_source={key_path}")
        print(f"recovery_ssh_key_fingerprint={fingerprint}")
        command.extend(["--recovery-authorized-key-file", str(key_path)])

    completed = subprocess.run([*command, *arguments], cwd=repo_root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
