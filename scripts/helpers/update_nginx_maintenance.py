#!/usr/bin/env python3
"""Ensure nginx configs serve the maintenance fallback page."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

MAINT_ERROR_LINE = "error_page 500 502 503 504 /maintenance/index.html;"
FILE_BLOCK = """location = /maintenance/index.html {
    root /usr/share/arthexis-fallback;
    add_header Cache-Control \"no-store\";
}"""
DIR_BLOCK = """location /maintenance/ {
    alias /usr/share/arthexis-fallback/;
    add_header Cache-Control \"no-store\";
}"""
PROXY_TARGET = "proxy_pass http://127.0.0.1"


def iter_blocks(text: str, keyword: str):
    """Yield (start, end, block) tuples for ``keyword { ... }`` blocks."""
    idx = 0
    while True:
        start = text.find(keyword, idx)
        if start == -1:
            break
        brace = text.find("{", start)
        if brace == -1:
            break
        depth = 1
        pos = brace + 1
        while depth > 0 and pos < len(text):
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        if depth != 0:
            break
        yield start, pos, text[start:pos]
        idx = pos


def ensure_blocks(block: str) -> tuple[str, bool]:
    start_brace = block.find("{")
    end_brace = block.rfind("}")
    if start_brace == -1 or end_brace == -1:
        return block, False

    inner = block[start_brace + 1 : end_brace]
    if PROXY_TARGET not in inner:
        return block, False

    changed = False

    if MAINT_ERROR_LINE not in inner:
        idx = inner.find("server_name")
        if idx != -1:
            end_line = inner.find("\n", idx)
            if end_line == -1:
                end_line = len(inner)
            inner = (
                inner[:end_line]
                + "\n    "
                + MAINT_ERROR_LINE
                + inner[end_line:]
            )
        else:
            inner = "\n    " + MAINT_ERROR_LINE + inner
        changed = True

    indented_file = textwrap.indent(FILE_BLOCK, "    ")
    if "location = /maintenance/index.html" not in inner:
        marker = "    location / {"
        if marker in inner:
            inner = inner.replace(marker, f"{indented_file}\n\n{marker}", 1)
        else:
            inner = inner.rstrip() + "\n" + indented_file + "\n"
        changed = True

    indented_dir = textwrap.indent(DIR_BLOCK, "    ")
    if "location /maintenance/ {" not in inner:
        marker = "    location / {"
        if marker in inner:
            inner = inner.replace(marker, f"{indented_dir}\n\n{marker}", 1)
        else:
            inner = inner.rstrip() + "\n" + indented_dir + "\n"
        changed = True

    proxy_added = False

    def add_proxy(match):
        nonlocal proxy_added
        head, body, tail = match.groups()
        if PROXY_TARGET not in body or "proxy_intercept_errors" in body:
            return match.group(0)
        idx = body.find("proxy_pass")
        end_line = body.find("\n", idx)
        if end_line == -1:
            end_line = len(body)
        insertion = "\n        proxy_intercept_errors on;"
        proxy_added = True
        return head + body[:end_line] + insertion + body[end_line:] + tail

    import re

    inner, _ = re.subn(r"(    location / \{)(.*?)(\n\s*\})", add_proxy, inner, flags=re.DOTALL)
    if proxy_added:
        changed = True

    if not inner.endswith("\n"):
        inner += "\n"

    new_block = block[: start_brace + 1] + inner + block[end_brace:]
    return new_block, changed


def update_config(path: Path) -> int:
    if not path.exists():
        return 0

    original = path.read_text()
    updated = original
    changed = False

    blocks = list(iter_blocks(updated, "server"))
    for start, end, block in reversed(blocks):
        new_block, block_changed = ensure_blocks(block)
        if block_changed:
            updated = updated[:start] + new_block + updated[end:]
            changed = True

    if changed and updated != original:
        path.write_text(updated)
        print(f"Updated maintenance fallback in {path}")
        return 2

    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: update_nginx_maintenance.py /path/to/conf", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    try:
        return update_config(path)
    except Exception as exc:  # pragma: no cover - defensive guard
        print(f"Failed to update {path}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
