import ast
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.protocols.services import load_protocol_spec_from_file, spec_path
from utils.coverage import coverage_color, render_badge


def _load_spec() -> dict[str, list[str]]:
    data = load_protocol_spec_from_file(spec_path("ocpp16"))
    return data["calls"]


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


def _collect_actions_from_dict(node: ast.Assign, target_name: str) -> set[str]:
    if not any(
        isinstance(target, ast.Name) and target.id == target_name
        for target in node.targets
    ):
        return set()
    if not isinstance(node.value, ast.Dict):
        return set()
    actions: set[str] = set()
    for key in node.value.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            actions.add(key.value)
    return actions


def _implemented_cp_to_csms(app_dir: Path) -> set[str]:
    """Collect implemented CP->CSMS actions from dispatch and routing modules."""

    source_paths = [
        candidate
        for candidate in (
            app_dir / "consumers" / "base" / "dispatch.py",
            app_dir / "consumers" / "base" / "consumer.py",
            app_dir / "consumers" / "base.py",
            app_dir / "consumers" / "base" / "consumer" / "routing.py",
        )
        if candidate.exists()
    ]
    if not source_paths:
        return set()

    class Visitor(ast.NodeVisitor):
        """AST visitor that extracts action names from known dispatch patterns."""

        def __init__(self) -> None:
            self.actions: set[str] = set()
            self._in_call_handler = False

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node.name in {"CSMSConsumer", "DispatchMixin", "ActionRouter"}:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in {
                        "receive",
                        "_handle_call_message",
                        "_build_registry",
                    }:
                        self.visit(item)
                return
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            self.actions.update(_collect_actions_from_compare(node, "action"))
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            if self._in_call_handler:
                self.actions.update(
                    _collect_actions_from_dict(node, "action_handlers")
                )
            self.generic_visit(node)

        def visit_Dict(self, node: ast.Dict) -> None:
            if self._in_call_handler:
                self.actions.update(
                    key.value
                    for key in node.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name in {"_handle_call_message", "_build_registry"}:
                previous_state = self._in_call_handler
                self._in_call_handler = True
                self.generic_visit(node)
                self._in_call_handler = previous_state
                return
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node.name in {"_handle_call_message", "_build_registry"}:
                previous_state = self._in_call_handler
                self._in_call_handler = True
                self.generic_visit(node)
                self._in_call_handler = previous_state
                return
            self.generic_visit(node)

    actions: set[str] = set()
    for source_path in source_paths:
        visitor = Visitor()
        visitor.visit(ast.parse(source_path.read_text(encoding="utf-8")))
        actions.update(visitor.actions)
    return actions


class _CsmsToCpVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.actions: set[str] = set()
        self._constant_stack: list[dict[str, set[str]]] = [dict()]

    def _current_constants(self) -> dict[str, set[str]]:
        return self._constant_stack[-1]

    def _push_scope(self) -> None:
        self._constant_stack.append(dict())

    def _pop_scope(self) -> None:
        self._constant_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._push_scope()
        self.generic_visit(node)
        self._pop_scope()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._push_scope()
        self.generic_visit(node)
        self._pop_scope()

    def visit_Assign(self, node: ast.Assign) -> None:
        if not node.targets:
            return

        if isinstance(node.value, ast.Constant) and isinstance(
            node.value.value, str
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._current_constants().setdefault(target.id, set()).add(
                        node.value.value
                    )

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
        action_values: set[str] = set()
        if isinstance(action_expr, ast.Constant) and isinstance(
            action_expr.value, str
        ):
            action_values.add(action_expr.value)
        elif isinstance(action_expr, ast.Name):
            action_values.update(self._current_constants().get(action_expr.id, set()))
        self.actions.update(action_values)


def _collect_csms_to_cp_actions(source: str) -> set[str]:
    visitor = _CsmsToCpVisitor()
    visitor.visit(ast.parse(source))
    return visitor.actions


def _implemented_csms_to_cp(app_dir: Path) -> set[str]:
    actions: set[str] = set()
    candidate_files: list[Path] = []

    for filename in ("views.py", "admin.py", "tasks.py"):
        path = app_dir / filename
        if path.exists():
            candidate_files.append(path)

    admin_pkg = app_dir / "admin"
    if admin_pkg.is_dir():
        candidate_files.extend(admin_pkg.glob("*.py"))

    views_pkg = app_dir / "views"
    if views_pkg.is_dir():
        candidate_files.extend(views_pkg.glob("*.py"))
        actions_pkg = views_pkg / "actions"
        if actions_pkg.is_dir():
            candidate_files.extend(actions_pkg.glob("*.py"))

    for path in candidate_files:
        actions.update(_collect_csms_to_cp_actions(path.read_text(encoding="utf-8")))

    return actions


def run_coverage_ocpp16(*, badge_path=None, json_path=None, stdout=None, stderr=None) -> None:
    """Generate OCPP 1.6 coverage output and badge."""
    app_dir = Path(__file__).resolve().parents[2]
    project_root = app_dir.parent.parent
    spec = _load_spec()

    implemented_cp_to_csms = _implemented_cp_to_csms(app_dir)
    implemented_csms_to_cp = _implemented_csms_to_cp(app_dir)

    spec_cp_to_csms = set(spec["cp_to_csms"])
    spec_csms_to_cp = set(spec["csms_to_cp"])

    cp_to_csms_coverage = sorted(spec_cp_to_csms & implemented_cp_to_csms)
    csms_to_cp_coverage = sorted(spec_csms_to_cp & implemented_csms_to_cp)

    cp_to_csms_percentage = (
        len(cp_to_csms_coverage) / len(spec_cp_to_csms) * 100 if spec_cp_to_csms else 0.0
    )
    csms_to_cp_percentage = (
        len(csms_to_cp_coverage) / len(spec_csms_to_cp) * 100 if spec_csms_to_cp else 0.0
    )

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

    badge_output = badge_path
    if badge_output is None:
        badge_output = project_root / "media" / "ocpp_coverage.svg"
    else:
        badge_output = Path(badge_output)
        if not badge_output.is_absolute():
            badge_output = project_root / badge_output
    badge_output.parent.mkdir(parents=True, exist_ok=True)

    badge_value = f"{round(overall_percentage, 1)}%"
    badge_svg = render_badge("ocpp 1.6", badge_value, coverage_color(overall_percentage))
    badge_output.write_text(badge_svg + "\n", encoding="utf-8")

    if overall_percentage < 100 and stderr:
        stderr.write("OCPP 1.6 coverage is incomplete; consider adding more handlers.")
        stderr.write(
            f"Currently supporting {len(overall_coverage)} of {len(overall_spec)} operations."
        )
    if stdout:
        stdout.write("Command completed without failure.")


class Command(BaseCommand):
    help = "Compute OCPP 1.6 call coverage and generate a badge."

    def add_arguments(self, parser) -> None:
        """Register arguments for legacy compatibility."""
        from apps.ocpp.management.commands._ocpp_command_helpers import add_coverage_arguments

        add_coverage_arguments(parser)

    def handle(self, *args, **options):
        from django.core.management import call_command

        from apps.ocpp.management.commands._ocpp_command_helpers import warn_deprecated_command

        warn_deprecated_command("coverage_ocpp16", "ocpp coverage --version 1.6")
        command_options = {
            key: value
            for key, value in options.items()
            if key in {"badge_path", "json_path"} and value is not None
        }
        call_command(
            "ocpp",
            "coverage",
            "--version",
            "1.6",
            stdout=self.stdout,
            stderr=self.stderr,
            **command_options,
        )
