#!/usr/bin/env python3
"""Preview or write the local console AGENTS.md for the new framework."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CONTENT = """# Console Instructions

- This console follows the Arthexis SKILLS, AGENTS, and HOOKS framework.
- `OPERATOR` means the human using an LLM-assisted session. Do not model `OPERATOR` and `AGENT` as separate local actors.
- Do not require the retired operator manual, and do not require `workgroup.md` ownership bookkeeping.
- Use the static `AGENTS.md` in the active repo plus any generated local `AGENTS.md` produced by the suite, such as `C:\\Users\\arthexis\\Repos\\arthexis\\work\\codex\\AGENTS.md` when present.
- Select skills by user intent or exact skill name. Prefer suite commands, skill scripts, and deterministic hooks over prose-only recipes.
- Treat `AGENT` as suite-provided context selected by node role and features, not as a personality, nickname, workgroup role, or sub-agent identity.
- Treat hooks as deterministic commands exposed by the suite for repeatable setup, validation, and integration points.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path.home() / "AGENTS.md")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    target = args.target.expanduser()
    before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    result: dict[str, Any] = {"target": str(target), "exists": target.exists(), "inSync": before == CONTENT, "write": args.write}
    if args.write and before != CONTENT:
        target.write_text(CONTENT, encoding="utf-8")
        result["written"] = True
    elif args.write:
        result["written"] = False
    result["content"] = CONTENT
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
