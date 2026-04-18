import ast
import json
from pathlib import Path

from apps.ocpp.management.coverage_ocpp16_impl import (
    _implemented_cp_to_csms,
    _implemented_csms_to_cp,
)
from apps.protocols.services import load_protocol_spec_from_file, spec_path
from utils.coverage import coverage_color, render_badge


def _load_spec() -> dict[str, list[str]]:
    """Load OCPP 2.0.1 call definitions from protocol specs."""
    data = load_protocol_spec_from_file(spec_path("ocpp201"))
    return data["calls"]


def _is_not_implemented_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    statements = list(node.body)
    if statements and isinstance(statements[0], ast.Expr) and isinstance(
        statements[0].value, ast.Constant
    ) and isinstance(statements[0].value.value, str):
        statements = statements[1:]
    if len(statements) != 1 or not isinstance(statements[0], ast.Raise):
        return False
    exception = statements[0].exc
    if isinstance(exception, ast.Name):
        return exception.id == "NotImplementedError"
    if isinstance(exception, ast.Call):
        func = exception.func
        return isinstance(func, ast.Name) and func.id == "NotImplementedError"
    return False


def _collect_real_decorated_actions(app_dir: Path, protocol_slug: str) -> tuple[set[str], set[str]]:
    """Collect protocol actions mapped to non-stub handlers for the target protocol."""

    cp_to_csms: set[str] = set()
    csms_to_cp: set[str] = set()

    for path in app_dir.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _is_not_implemented_stub(node):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                is_protocol_call = (
                    isinstance(func, ast.Name) and func.id == "protocol_call"
                ) or (
                    isinstance(func, ast.Attribute) and func.attr == "protocol_call"
                )
                if not is_protocol_call or len(decorator.args) < 3:
                    continue
                slug_arg = decorator.args[0]
                direction_arg = decorator.args[1]
                action_arg = decorator.args[2]
                if not (
                    isinstance(slug_arg, ast.Constant)
                    and isinstance(slug_arg.value, str)
                    and slug_arg.value == protocol_slug
                ):
                    continue
                if not (
                    isinstance(action_arg, ast.Constant) and isinstance(action_arg.value, str)
                ):
                    continue
                if (
                    isinstance(direction_arg, ast.Constant)
                    and direction_arg.value == "cp_to_csms"
                ) or (
                    isinstance(direction_arg, ast.Attribute)
                    and direction_arg.attr == "CP_TO_CSMS"
                ):
                    cp_to_csms.add(action_arg.value)
                elif (
                    isinstance(direction_arg, ast.Constant)
                    and direction_arg.value == "csms_to_cp"
                ) or (
                    isinstance(direction_arg, ast.Attribute)
                    and direction_arg.attr == "CSMS_TO_CP"
                ):
                    csms_to_cp.add(action_arg.value)
    return cp_to_csms, csms_to_cp


def run_coverage_ocpp201(*, badge_path=None, json_path=None, stdout=None, stderr=None) -> None:
    """Generate OCPP 2.0.1 coverage output and badge."""
    app_dir = Path(__file__).resolve().parents[1]
    project_root = app_dir.parent.parent
    spec = _load_spec()
    implemented_cp_to_csms = _implemented_cp_to_csms(app_dir)
    implemented_csms_to_cp = _implemented_csms_to_cp(app_dir)
    real_cp_to_csms, real_csms_to_cp = _collect_real_decorated_actions(app_dir, "ocpp201")
    implemented_cp_to_csms |= real_cp_to_csms
    implemented_csms_to_cp |= real_csms_to_cp
    spec_cp_to_csms = set(spec["cp_to_csms"])
    spec_csms_to_cp = set(spec["csms_to_cp"])
    cp_to_csms_coverage = sorted(spec_cp_to_csms & implemented_cp_to_csms)
    csms_to_cp_coverage = sorted(spec_csms_to_cp & implemented_csms_to_cp)
    cp_to_csms_percentage = len(cp_to_csms_coverage) / len(spec_cp_to_csms) * 100 if spec_cp_to_csms else 0.0
    csms_to_cp_percentage = len(csms_to_cp_coverage) / len(spec_csms_to_cp) * 100 if spec_csms_to_cp else 0.0
    overall_spec = spec_cp_to_csms | spec_csms_to_cp
    overall_implemented = implemented_cp_to_csms | implemented_csms_to_cp
    overall_coverage = sorted(overall_spec & overall_implemented)
    overall_percentage = len(overall_coverage) / len(overall_spec) * 100 if overall_spec else 0.0
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
    if stdout:
        stdout.write(output)
    if json_path:
        path = Path(json_path)
        if not path.is_absolute():
            path = project_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")
    badge_output = Path(badge_path) if badge_path else project_root / "media" / "ocpp201_coverage.svg"
    if not badge_output.is_absolute():
        badge_output = project_root / badge_output
    badge_output.parent.mkdir(parents=True, exist_ok=True)
    badge_output.write_text(
        render_badge("ocpp 2.0.1", f"{round(overall_percentage, 1)}%", coverage_color(overall_percentage)) + "\n",
        encoding="utf-8",
    )
    if overall_percentage < 100 and stderr:
        stderr.write("OCPP 2.0.1 coverage is incomplete; consider adding more handlers.")
        stderr.write(f"Currently supporting {len(overall_coverage)} of {len(overall_spec)} operations.")
    if stdout:
        stdout.write("Command completed without failure.")
