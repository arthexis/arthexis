import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class CalculateCoverageCommandTests(SimpleTestCase):
    def test_generates_summary_and_badge(self):
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            coverage_json = base / "coverage.json"
            badge_path = base / "badges" / "coverage.svg"
            coverage_json.write_text(
                json.dumps(
                    {
                        "totals": {
                            "covered_lines": 90,
                            "missing_lines": 10,
                            "num_statements": 100,
                        }
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            call_command(
                "calculate_coverage",
                coverage_json=str(coverage_json),
                badge_path=str(badge_path),
                label="tests",
                stdout=stdout,
            )

            output = json.loads(stdout.getvalue())
            assert output == {
                "covered_lines": 90,
                "missing_lines": 10,
                "num_statements": 100,
                "percent_covered": 90.0,
            }

            badge_content = badge_path.read_text(encoding="utf-8")
            assert "tests" in badge_content
            assert "90.0%" in badge_content

    def test_raises_error_for_missing_report(self):
        with TemporaryDirectory() as tmpdir:
            coverage_json = Path(tmpdir) / "missing.json"
            with self.assertRaisesMessage(
                CommandError, f"Coverage report {coverage_json} does not exist"
            ):
                call_command("calculate_coverage", coverage_json=str(coverage_json))

    def test_rejects_invalid_json(self):
        with TemporaryDirectory() as tmpdir:
            coverage_json = Path(tmpdir) / "invalid.json"
            coverage_json.write_text("not-json", encoding="utf-8")

            with self.assertRaisesMessage(
                CommandError, "Coverage report"  # propagated from load_summary
            ):
                call_command("calculate_coverage", coverage_json=str(coverage_json))
