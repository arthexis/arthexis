import ast
import json
from pathlib import Path

from django.core.management.base import BaseCommand


def _load_spec() -> dict[str, list[str]]:
    app_dir = Path(__file__).resolve().parents[2]
    spec_path = app_dir / "spec" / "ocpp16_calls.json"
    with spec_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        "cp_to_csms": list(dict.fromkeys(data.get("cp_to_csms", []))),
        "csms_to_cp": list(dict.fromkeys(data.get("csms_to_cp", []))),
    }


def _collect_actions_from_compare(node: ast.Compare, target_name: str) -> set[str]:
    def is_target(expr: ast.AST) -> bool:
        return isinstance(expr, ast.Name) and expr.id == target_name

    if not node.ops or not isinstance(node.ops[0], ast.Eq):
        return set()

    values: set[str] = set()
    if is_target(node.left):
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(
                comparator.value, str
            ):
                values.add(comparator.value)
    elif any(is_target(comparator) for comparator in node.comparators):
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            values.add(node.left.value)
    return values


def _implemented_cp_to_csms(app_dir: Path) -> set[str]:
    source = (app_dir / "consumers.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.actions: set[str] = set()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node.name == "CSMSConsumer":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "receive":
                        self.visit(item)
                        return
            # Continue walking in case nested classes exist.
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            self.actions.update(_collect_actions_from_compare(node, "action"))
            self.generic_visit(node)

    visitor = Visitor()
    visitor.visit(tree)
    return visitor.actions


def _implemented_csms_to_cp(app_dir: Path) -> set[str]:
    source = (app_dir / "views.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.actions: set[str] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name == "dispatch_action":
                self.generic_visit(node)
            # Skip other functions by default.

        def visit_Assign(self, node: ast.Assign) -> None:
            if not node.targets:
                return
            if not any(
                isinstance(target, ast.Name) and target.id == "msg"
                for target in node.targets
            ):
                return
            value = node.value
            if not isinstance(value, ast.Call):
                return
            func = value.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
                and func.attr == "dumps"
            ):
                return
            if not value.args:
                return
            payload = value.args[0]
            if not isinstance(payload, ast.List) or len(payload.elts) < 3:
                return
            action_expr = payload.elts[2]
            if isinstance(action_expr, ast.Constant) and isinstance(
                action_expr.value, str
            ):
                self.actions.add(action_expr.value)

    visitor = Visitor()
    visitor.visit(tree)
    return visitor.actions


def _coverage_color(percentage: float) -> str:
    if percentage >= 90:
        return "#4c1"
    if percentage >= 75:
        return "#97CA00"
    if percentage >= 60:
        return "#dfb317"
    if percentage >= 40:
        return "#fe7d37"
    return "#e05d44"


def _render_badge(label: str, value: str, color: str) -> str:
    label_width = 6 * len(label) + 20
    value_width = 6 * len(value) + 20
    total_width = label_width + value_width
    return f"""
<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{total_width}\" height=\"20\" role=\"img\" aria-label=\"{label}: {value}\">
  <title>{label}: {value}</title>
  <linearGradient id=\"s\" x2=\"0\" y2=\"100%\">
    <stop offset=\"0\" stop-color=\"#bbb\" stop-opacity=\".1\"/>
    <stop offset=\"1\" stop-opacity=\".1\"/>
  </linearGradient>
  <clipPath id=\"r\">
    <rect width=\"{total_width}\" height=\"20\" rx=\"3\" fill=\"#fff\"/>
  </clipPath>
  <g clip-path=\"url(#r)\">
    <rect width=\"{label_width}\" height=\"20\" fill=\"#555\"/>
    <rect x=\"{label_width}\" width=\"{value_width}\" height=\"20\" fill=\"{color}\"/>
    <rect width=\"{total_width}\" height=\"20\" fill=\"url(#s)\"/>
  </g>
  <g fill=\"#fff\" text-anchor=\"middle\" font-family=\"Verdana,Geneva,DejaVu Sans,sans-serif\" font-size=\"11\">
    <text x=\"{label_width / 2:.1f}\" y=\"14\">{label}</text>
    <text x=\"{label_width + value_width / 2:.1f}\" y=\"14\">{value}</text>
  </g>
</svg>
""".strip()


class Command(BaseCommand):
    help = "Compute OCPP 1.6 call coverage and generate a badge."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--badge-path",
            default=None,
            help="Optional path to write the SVG badge. Defaults to project root ocpp_coverage.svg.",
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
            badge_path = project_root / "ocpp_coverage.svg"
        else:
            badge_path = Path(badge_path)
            if not badge_path.is_absolute():
                badge_path = project_root / badge_path
            badge_path.parent.mkdir(parents=True, exist_ok=True)

        badge_value = f"{round(overall_percentage, 1)}%"
        badge_label = "ocpp 1.6"
        badge_color = _coverage_color(overall_percentage)
        badge_svg = _render_badge(badge_label, badge_value, badge_color)
        badge_path.write_text(badge_svg + "\n", encoding="utf-8")

        if overall_percentage < 100:
            self.stderr.write(
                "OCPP 1.6 coverage is incomplete; consider adding more handlers."
            )
            self.stderr.write(
                f"Currently supporting {len(overall_coverage)} of {len(overall_spec)} operations."
            )
            self.stderr.write("Command completed without failure.")
