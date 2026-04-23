from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

def _run_lcd_configured_check(
    tmp_path: Path,
    *,
    systemd_units: str = "",
    service_mode: str = "systemd",
    service_name: str = "suite",
) -> subprocess.CompletedProcess[str]:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    if systemd_units:
        (lock_dir / "systemd_services.lck").write_text(systemd_units, encoding="utf-8")

    helper_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "helpers"
        / "service_manager.sh"
    )
    script = f"""
set -euo pipefail
source {shlex.quote(str(helper_path))}
if arthexis_lcd_service_configured {shlex.quote(str(lock_dir))} {shlex.quote(service_name)} {shlex.quote(service_mode)}; then
  echo configured
else
  echo disabled
fi
"""

    return subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )

