import ast
import json
from collections.abc import Iterator
from pathlib import Path

from apps.ocpp.management.coverage_ocpp16_impl import (
    _implemented_cp_to_csms,
    _implemented_csms_to_cp,
)
from apps.protocols.services import load_protocol_spec_from_file, spec_path
from utils.coverage import coverage_color, render_badge

DIRECTION_NAMES = {"cp_to_csms": "cp_to_csms", "csms_to_cp": "csms_to_cp"}
DIRECTION_ATTRIBUTES = {"CP_TO_CSMS": "cp_to_csms", "CSMS_TO_CP": "csms_to_cp"}


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


def _extract_constant_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_protocol_call_func(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "protocol_call"
    if isinstance(node, ast.Attribute):
        return node.attr == "protocol_call"
    return False


def _normalize_direction(node: ast.AST) -> str | None:
    constant_value = _extract_constant_str(node)
    if constant_value in DIRECTION_NAMES:
        return DIRECTION_NAMES[constant_value]
    if isinstance(node, ast.Attribute):
        return DIRECTION_ATTRIBUTES.get(node.attr)
    return None


def _decorator_argument(
    decorator: ast.Call, index: int, keyword: str
) -> ast.AST | None:
    if len(decorator.args) > index:
        return decorator.args[index]
    return next(
        (kw.value for kw in decorator.keywords if kw.arg == keyword),
        None,
    )


def _protocol_call_details(
    decorator: ast.expr, protocol_slug: str
) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None
    if not _is_protocol_call_func(decorator.func):
        return None
    slug_arg = _decorator_argument(decorator, 0, "protocol_slug")
    direction_arg = _decorator_argument(decorator, 1, "direction")
    action_arg = _decorator_argument(decorator, 2, "call_name")
    if slug_arg is None or direction_arg is None or action_arg is None:
        return None
    slug = _extract_constant_str(slug_arg)
    direction = _normalize_direction(direction_arg)
    action = _extract_constant_str(action_arg)
    if slug != protocol_slug or direction is None or action is None:
        return None
    return direction, action


def _iter_decorated_actions(
    app_dir: Path, protocol_slug: str
) -> Iterator[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, Path, bool]]:
    for path in app_dir.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_stub = _is_not_implemented_stub(node)
            for decorator in node.decorator_list:
                details = _protocol_call_details(decorator, protocol_slug)
                if details is None:
                    continue
                direction, action = details
                yield direction, action, node, path, is_stub


def _collect_stub_decorated_actions(
    app_dir: Path, protocol_slug: str
) -> list[dict[str, object]]:
    """Collect protocol actions still mapped directly to NotImplementedError stubs."""

    stubs: list[dict[str, object]] = []
    for direction, action, node, path, is_stub in _iter_decorated_actions(
        app_dir, protocol_slug
    ):
        if not is_stub:
            continue
        stubs.append(
            {
                "action": action,
                "direction": direction,
                "function": node.name,
                "line": node.lineno,
                "path": path.relative_to(app_dir).as_posix(),
            }
        )
    return sorted(
        stubs,
        key=lambda stub: (
            str(stub["direction"]),
            str(stub["action"]),
            str(stub["path"]),
            str(stub["function"]),
        ),
    )


def _collect_real_decorated_actions(app_dir: Path, protocol_slug: str) -> tuple[set[str], set[str]]:
    """Collect protocol actions mapped to non-stub handlers for the target protocol."""

    cp_to_csms: set[str] = set()
    csms_to_cp: set[str] = set()

    for direction, action, _node, _path, is_stub in _iter_decorated_actions(
        app_dir, protocol_slug
    ):
        if is_stub:
            continue
        if direction == "cp_to_csms":
            cp_to_csms.add(action)
        elif direction == "csms_to_cp":
            csms_to_cp.add(action)
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
    missing_cp_to_csms = sorted(spec_cp_to_csms - implemented_cp_to_csms)
    missing_csms_to_cp = sorted(spec_csms_to_cp - implemented_csms_to_cp)
    stubbed_actions = _collect_stub_decorated_actions(app_dir, "ocpp201")
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
        "missing": {
            "cp_to_csms": missing_cp_to_csms,
            "csms_to_cp": missing_csms_to_cp,
            "overall": sorted(set(missing_cp_to_csms) | set(missing_csms_to_cp)),
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
        "stubbed": stubbed_actions,
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
    if stubbed_actions and stderr:
        stderr.write("OCPP 2.0.1 decorated handlers still contain NotImplementedError stubs.")
    if stdout:
        stdout.write("Command completed without failure.")
