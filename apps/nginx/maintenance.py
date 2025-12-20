from __future__ import annotations

import re
import textwrap
from pathlib import Path

ERROR_LINES = (
    "error_page 404 /maintenance/404.html;",
    "error_page 500 502 503 504 /maintenance/app-down.html;",
)
INDEX_BLOCK = """location = /maintenance/index.html {
    root /usr/share/arthexis-fallback;
    add_header Cache-Control \"no-store\";
}"""
NOT_FOUND_BLOCK = """location = /maintenance/404.html {
    root /usr/share/arthexis-fallback;
    add_header Cache-Control \"no-store\";
}"""
DOWN_BLOCK = """location = /maintenance/app-down.html {
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
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(keyword)}\s*\{{")
    for match in pattern.finditer(text):
        start = match.start()
        brace = text.find("{", match.end() - 1)
        if brace == -1:
            continue
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
            continue
        yield start, pos, text[start:pos]


def normalize_orphaned_server_block(text: str) -> tuple[str, bool]:
    stripped = text.lstrip()
    if stripped.startswith("server {"):
        return text, False

    if not stripped.startswith(("listen", "location")):
        return text, False

    first_server = text.find("server {")
    if first_server == -1:
        wrapped = f"server {{\n{text.rstrip()}\n}}\n"
        return wrapped, True

    prefix = text[:first_server]
    suffix = text[first_server:]
    wrapped = f"server {{\n{prefix.rstrip()}\n}}\n\n{suffix.lstrip()}"
    return wrapped, True


def ensure_blocks(block: str) -> tuple[str, bool]:
    start_brace = block.find("{")
    end_brace = block.rfind("}")
    if start_brace == -1 or end_brace == -1:
        return block, False

    inner = block[start_brace + 1 : end_brace]
    if PROXY_TARGET not in inner:
        return block, False

    changed = False

    missing_error_lines = [line for line in ERROR_LINES if line not in inner]
    if missing_error_lines:
        idx = inner.find("server_name")
        if idx != -1:
            end_line = inner.find("\n", idx)
            if end_line == -1:
                end_line = len(inner)
            insertion = "".join(f"\n    {line}" for line in missing_error_lines)
            inner = inner[:end_line] + insertion + inner[end_line:]
        else:
            insertion = "".join(f"\n    {line}" for line in missing_error_lines)
            inner = insertion + inner
        changed = True

    location_marker = "    location / {"
    indented_dir = textwrap.indent(DIR_BLOCK, "    ")
    file_blocks = (
        ("location = /maintenance/index.html", INDEX_BLOCK),
        ("location = /maintenance/404.html", NOT_FOUND_BLOCK),
        ("location = /maintenance/app-down.html", DOWN_BLOCK),
    )

    for identifier, snippet in file_blocks:
        if identifier not in inner:
            indented_block = textwrap.indent(snippet, "    ")
            if location_marker in inner:
                inner = inner.replace(location_marker, f"{indented_block}\n\n{location_marker}", 1)
            else:
                inner = inner.rstrip() + "\n" + indented_block + "\n"
            changed = True

    if "location /maintenance/ {" not in inner:
        if location_marker in inner:
            inner = inner.replace(location_marker, f"{indented_dir}\n\n{location_marker}", 1)
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

    updated, normalized = normalize_orphaned_server_block(updated)
    if normalized:
        changed = True

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
