import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.ocpp.management.commands.coverage_ocpp16 import (
    _implemented_cp_to_csms,
    _implemented_csms_to_cp,
)
from apps.protocols.services import load_protocol_spec_from_file, spec_path
from utils.coverage import coverage_color, render_badge


def _load_spec() -> dict[str, list[str]]:
    data = load_protocol_spec_from_file(spec_path("ocpp201"))
    return data["calls"]


class Command(BaseCommand):
    help = "Compute OCPP 2.0.1 call coverage and generate a badge."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--badge-path",
            default=None,
            help="Optional path to write the SVG badge. Defaults to project media/ocpp201_coverage.svg.",
        )
        parser.add_argument(
            "--json-path",
            default=None,
            help="Optional path to write the JSON summary.",
        )

    def handle(self, *args, **options):
        app_dir = Path(__file__).resolve().parents[2]
        project_root = app_dir.parent
        spec = _load_spec()

        implemented_cp_to_csms = _implemented_cp_to_csms(app_dir)
        implemented_csms_to_cp = _implemented_csms_to_cp(app_dir)

        spec_cp_to_csms = set(spec["cp_to_csms"])
        spec_csms_to_cp = set(spec["csms_to_cp"])

        cp_to_csms_coverage = sorted(spec_cp_to_csms & implemented_cp_to_csms)
        csms_to_cp_coverage = sorted(spec_csms_to_cp & implemented_csms_to_cp)

        cp_to_csms_percentage = (
            len(cp_to_csms_coverage) / len(spec_cp_to_csms) * 100
            if spec_cp_to_csms
            else 0.0
        )
        csms_to_cp_percentage = (
            len(csms_to_cp_coverage) / len(spec_csms_to_cp) * 100
            if spec_csms_to_cp
            else 0.0
        )

        overall_spec = spec_cp_to_csms | spec_csms_to_cp
        overall_implemented = implemented_cp_to_csms | implemented_csms_to_cp
        overall_coverage = sorted(overall_spec & overall_implemented)
        overall_percentage = (
            len(overall_coverage) / len(overall_spec) * 100 if overall_spec else 0.0
        )

        summary = {
            "spec": spec,
            "implemented": {
                "cp_to_csms": sorted(implemented_cp_to_csms),
                "csms_to_cp": sorted(implemented_csms_to_cp),
            },
            "coverage": {
                "cp_to_csms": {
                    "supported": cp_to_csms_coverage,
                    "count": len(cp_to_csms_coverage),
                    "total": len(spec_cp_to_csms),
                    "percent": round(cp_to_csms_percentage, 2),
                },
                "csms_to_cp": {
                    "supported": csms_to_cp_coverage,
                    "count": len(csms_to_cp_coverage),
                    "total": len(spec_csms_to_cp),
                    "percent": round(csms_to_cp_percentage, 2),
                },
                "overall": {
                    "supported": overall_coverage,
                    "count": len(overall_coverage),
                    "total": len(overall_spec),
                    "percent": round(overall_percentage, 2),
                },
            },
        }

        output = json.dumps(summary, indent=2, sort_keys=True)
        self.stdout.write(output)

        json_path = options.get("json_path")
        if json_path:
            path = Path(json_path)
            if not path.is_absolute():
                path = project_root / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output + "\n", encoding="utf-8")

        badge_path = options.get("badge_path")
        if badge_path is None:
            badge_path = project_root / "media" / "ocpp201_coverage.svg"
            badge_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            badge_path = Path(badge_path)
            if not badge_path.is_absolute():
                badge_path = project_root / badge_path
            badge_path.parent.mkdir(parents=True, exist_ok=True)

        badge_value = f"{round(overall_percentage, 1)}%"
        badge_label = "ocpp 2.0.1"
        badge_color = coverage_color(overall_percentage)
        badge_svg = render_badge(badge_label, badge_value, badge_color)
        badge_path.write_text(badge_svg + "\n", encoding="utf-8")

        if overall_percentage < 100:
            self.stderr.write(
                "OCPP 2.0.1 coverage is incomplete; consider adding more handlers."
            )
            self.stderr.write(
                f"Currently supporting {len(overall_coverage)} of {len(overall_spec)} operations."
            )
            self.stderr.write("Command completed without failure.")
