import json
import re
import subprocess
import sys
from pathlib import Path


def staged_diff(path: str) -> list[str]:
    return subprocess.run(
        ["git", "diff", "--cached", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def get_staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def parse_models(path: str) -> set[str]:
    models = set()
    current = None
    for line in staged_diff(path):
        if line.startswith("@@"):
            continue
        content = line[1:] if line[:1] in "+-" else line
        match = re.match(r"\s*class\s+(\w+)\(.*models.Model", content)
        if match:
            current = match.group(1)
            if line.startswith("+"):
                models.add(current)
            continue
        if line.startswith("+") and current:
            models.add(current)
    return models


def parse_views(path: str) -> set[str]:
    views = set()
    current = None
    for line in staged_diff(path):
        if line.startswith("@@"):
            continue
        content = line[1:] if line[:1] in "+-" else line
        match = re.match(r"\s*(?:def|class)\s+(\w+)", content)
        if match:
            current = match.group(1)
            if line.startswith("+"):
                views.add(current)
            continue
        if line.startswith("+") and current:
            views.add(current)
    return views


def parse_command(path: str) -> str | None:
    diff = staged_diff(path)
    if any(line.startswith("+") for line in diff):
        return Path(path).stem
    return None


def ensure_todo(entries: list, description: str) -> None:
    for item in entries:
        if item["fields"]["description"].lower() == description.lower():
            return
    next_pk = max((e["pk"] for e in entries), default=0) + 1
    entries.append(
        {
            "model": "core.todo",
            "pk": next_pk,
            "fields": {
                "description": description,
                "url": "",
                "request_details": "",
            },
        }
    )


def main() -> int:
    base = Path(__file__).resolve().parents[1]
    todos_dir = base / "apps" / "core" / "fixtures"
    entries = []
    for p in sorted(todos_dir.glob("todos__*.json")):
        entries.extend(json.loads(p.read_text()))
    original_len = len(entries)

    files = get_staged_files()
    for path in files:
        if not path.endswith(".py"):
            continue
        if path.endswith("models.py"):
            for model in parse_models(path):
                ensure_todo(entries, f"Validate model {model}")
        elif path.endswith("views.py"):
            for view in parse_views(path):
                ensure_todo(entries, f"Validate view {view}")
        elif "/management/commands/" in path:
            cmd = parse_command(path)
            if cmd:
                ensure_todo(entries, f"Validate command {cmd}")

    if len(entries) != original_len:
        for item in entries[original_len:]:
            path = todos_dir / f"todos__todo_{item['pk']}.json"
            path.write_text(json.dumps([item], indent=2) + "\n")
            subprocess.run(["git", "add", str(path)])
        print("Added validation TODOs.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
