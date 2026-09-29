from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowText:
    path: Path
    text: str

    @classmethod
    def load(cls, path: str | Path) -> "WorkflowText":
        resolved = Path(path)
        return cls(resolved, resolved.read_text(encoding="utf-8"))

    def job(self, job_id: str) -> str:
        marker = f"  {job_id}:"
        return self._indented_block(marker, indent=2)

    def step(self, name: str, *, job_id: str = "deploy") -> str:
        job = self.job(job_id)
        marker = f"      - name: {name}"
        return self._indented_block(marker, indent=6, text=job)

    @staticmethod
    def _indented_block(marker: str, *, indent: int, text: str | None = None) -> str:
        source = text if text is not None else ""
        if marker not in source:
            raise AssertionError(f"workflow block not found: {marker}")

        lines = source.splitlines()
        start = next(index for index, line in enumerate(lines) if line == marker)
        end = len(lines)
        prefix = " " * indent
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if line and not line.startswith(prefix + " "):
                if len(line) - len(line.lstrip()) <= indent:
                    end = index
                    break
        return "\n".join(lines[start:end]) + "\n"
