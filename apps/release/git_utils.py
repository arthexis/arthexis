from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit


def git_remote_url(
    remote: str = "origin",
    *,
    base_dir: Path | None = None,
    use_push_url: bool = False,
) -> Optional[str]:
    cmd = ["git", "remote", "get-url"]
    if use_push_url:
        cmd.append("--push")
    cmd.append(remote)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=base_dir,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def remote_url_with_credentials(
    url: str,
    *,
    username: str,
    password: str,
) -> Optional[str]:
    if not username or not password:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.split("@", 1)[-1]
    username = quote(username.strip(), safe="")
    password = quote(password.strip(), safe="")
    if not username or not password:
        return None
    netloc = f"{username}:{password}@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
