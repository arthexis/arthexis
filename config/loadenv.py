import logging
import os
import re
import subprocess
from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

_DPAPI_ENV_PREFIX = "ARTHEXIS_DPAPI_ENV_"
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _read_windows_dpapi_secret(path: str) -> str:
    script = r"""
$ErrorActionPreference = 'Stop'
$Path = $env:ARTHEXIS_DPAPI_CREDENTIAL_PATH
$secure = Get-Content -LiteralPath $Path | ConvertTo-SecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
"""
    env = os.environ.copy()
    env["ARTHEXIS_DPAPI_CREDENTIAL_PATH"] = path
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )
    return result.stdout.rstrip("\r\n")


def _load_dpapi_env_secrets() -> None:
    if os.name != "nt":
        return

    for source_key, credential_path in list(os.environ.items()):
        if not source_key.startswith(_DPAPI_ENV_PREFIX):
            continue
        target_key = source_key.removeprefix(_DPAPI_ENV_PREFIX)
        if not _ENV_NAME_RE.match(target_key):
            logger.warning("Ignoring invalid DPAPI environment target %s", target_key)
            continue
        if os.environ.get(target_key):
            continue
        if not credential_path:
            continue
        try:
            secret = _read_windows_dpapi_secret(credential_path)
        except Exception as exc:
            logger.warning(
                "Failed to load DPAPI environment secret for %s from %s: %s",
                target_key,
                credential_path,
                exc,
            )
            continue
        if secret:
            os.environ[target_key] = secret


def loadenv() -> None:
    """Load all .env files from the repository root."""
    for env_file in sorted(BASE_DIR.glob("*.env")):
        load_dotenv(env_file, override=False)
    _load_dpapi_env_secrets()
