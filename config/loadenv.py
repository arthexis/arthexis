import json
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


def _called_process_error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
        return exc.stderr.strip()
    return str(exc)


def _read_windows_dpapi_secrets(
    paths_by_target: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    script = r"""
$ErrorActionPreference = 'Stop'
$items = $env:ARTHEXIS_DPAPI_CREDENTIAL_PATHS | ConvertFrom-Json
$secrets = [ordered]@{}
$errors = [ordered]@{}
foreach ($property in $items.PSObject.Properties) {
    $target = $property.Name
    $Path = [string]$property.Value
    try {
        $secure = Get-Content -LiteralPath $Path | ConvertTo-SecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $secrets[$target] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        }
        finally {
            if ($bstr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }
    }
    catch {
        $errors[$target] = $_.Exception.Message
    }
}
[ordered]@{ secrets = $secrets; errors = $errors } | ConvertTo-Json -Compress -Depth 4
"""
    env = os.environ.copy()
    env["ARTHEXIS_DPAPI_CREDENTIAL_PATHS"] = json.dumps(paths_by_target)
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
    payload = json.loads(result.stdout or "{}")
    return (
        dict(payload.get("secrets") or {}),
        dict(payload.get("errors") or {}),
    )


def _load_dpapi_env_secrets() -> None:
    if os.name != "nt":
        return

    pending: dict[str, str] = {}
    for source_key, credential_path in list(os.environ.items()):
        if not source_key.startswith(_DPAPI_ENV_PREFIX):
            continue
        target_key = source_key.removeprefix(_DPAPI_ENV_PREFIX)
        if not _ENV_NAME_RE.match(target_key):
            logger.warning("Ignoring invalid DPAPI environment target %s", target_key)
            continue
        if target_key in os.environ:
            continue
        if not credential_path:
            continue
        pending[target_key] = credential_path

    if not pending:
        return

    try:
        secrets, errors = _read_windows_dpapi_secrets(pending)
    except Exception as exc:
        logger.warning(
            "Failed to load DPAPI environment secrets for %s: %s",
            ", ".join(sorted(pending)),
            _called_process_error_detail(exc),
        )
        return

    for target_key, error in errors.items():
        logger.warning(
            "Failed to load DPAPI environment secret for %s from %s: %s",
            target_key,
            pending.get(target_key, ""),
            error,
        )

    for target_key, secret in secrets.items():
        if target_key not in pending:
            logger.warning(
                "Ignoring DPAPI environment secret for unexpected target %s",
                target_key,
            )
            continue
        if target_key in os.environ:
            continue
        if secret:
            os.environ[target_key] = secret


def loadenv() -> None:
    """Load all .env files from the repository root."""
    for env_file in sorted(BASE_DIR.glob("*.env")):
        load_dotenv(env_file, override=False)
    _load_dpapi_env_secrets()
