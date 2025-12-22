"""Manually refresh node information and report the results."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.tasks import poll_peers


class Command(BaseCommand):
    """Update all nodes and display a compact status table."""

    help = "Refresh each node and show the outcome of local and remote updates."

    def handle(self, *args, **options):
        summary = poll_peers(enforce_feature=False)
        self._report_summary(summary)

    def _report_summary(self, summary: dict) -> None:
        if summary.get("skipped"):
            raise CommandError(summary.get("reason") or "Node refresh skipped")

        results = summary.get("results") or []
        if not results:
            self.stdout.write(self.style.WARNING("No nodes to refresh."))
            return

        self.stdout.write(self._build_table(results))
        self.stdout.write("")
        self.stdout.write(
            f"Total: {summary.get('total', 0)} "
            f"(success: {summary.get('success', 0)}, "
            f"partial: {summary.get('partial', 0)}, "
            f"error: {summary.get('error', 0)})"
        )

    def _build_table(self, results: list[dict]) -> str:
        headers = ["ID", "Node", "Status", "Local", "Remote"]
        rows: list[list[str]] = []

        for entry in results:
            rows.append(
                [
                    str(entry.get("node_id", "")),
                    str(entry.get("node", "")),
                    str(entry.get("status", "")),
                    self._format_result(entry.get("local")),
                    self._format_result(entry.get("remote")),
                ]
            )

        col_widths = [len(header) for header in headers]
        for row in rows:
            for index, cell in enumerate(row):
                col_widths[index] = max(col_widths[index], len(cell))

        def _render_row(row: list[str]) -> str:
            return " | ".join(cell.ljust(col_widths[idx]) for idx, cell in enumerate(row))

        separator = "-+-".join("-" * width for width in col_widths)
        lines = [_render_row(headers), separator]
        lines.extend(_render_row(row) for row in rows)
        return "\n".join(lines)

    def _format_result(self, result: dict | None) -> str:
        if not isinstance(result, dict):
            return ""

        status = "OK" if result.get("ok") else "ERROR"
        message = result.get("message") or result.get("detail") or ""

        updated_fields = result.get("updated_fields")
        if not message and updated_fields:
            if isinstance(updated_fields, (list, tuple)):
                message = f"updated: {', '.join(updated_fields)}"
            else:
                message = f"updated: {updated_fields}"

        return " ".join(part for part in (status, message) if part).strip()
