from dataclasses import dataclass
import ast
import re

from django.core.exceptions import AppRegistryNotReady
from django.db import models
from django.db.models.fields import DeferredAttribute
from django.utils.translation import gettext_lazy as _


class _BaseSigilDescriptor(DeferredAttribute):
    def __set__(self, instance, value):
        instance.__dict__[self.field.attname] = value


class _CheckSigilDescriptor(_BaseSigilDescriptor):
    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        if getattr(instance, f"{self.field.name}_resolve_sigils", False):
            return instance.resolve_sigils(self.field.name)
        return value


class _AutoSigilDescriptor(_BaseSigilDescriptor):
    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        return instance.resolve_sigils(self.field.name)


class _SigilBaseField:
    def value_from_object(self, obj):
        return obj.__dict__.get(self.attname)

    def pre_save(self, model_instance, add):
        # ``models.Field.pre_save`` uses ``getattr`` which would resolve the
        # sigil descriptor. Persist the raw database value instead so env-based
        # placeholders remain intact when editing through admin forms.
        return self.value_from_object(model_instance)


class SigilCheckFieldMixin(_SigilBaseField):
    descriptor_class = _CheckSigilDescriptor

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)
        extra_name = f"{name}_resolve_sigils"
        if not any(f.name == extra_name for f in cls._meta.fields):
            cls.add_to_class(
                extra_name,
                models.BooleanField(
                    default=False,
                    verbose_name="Resolve [SIGILS] in templates",
                ),
            )


class SigilAutoFieldMixin(_SigilBaseField):
    descriptor_class = _AutoSigilDescriptor

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)


class SigilShortCheckField(SigilCheckFieldMixin, models.CharField):
    pass


class SigilLongCheckField(SigilCheckFieldMixin, models.TextField):
    pass


class SigilShortAutoField(SigilAutoFieldMixin, models.CharField):
    pass


class SigilLongAutoField(SigilAutoFieldMixin, models.TextField):
    pass


class ConditionEvaluationError(Exception):
    """Raised when a condition expression cannot be evaluated."""


@dataclass
class ConditionCheckResult:
    """Represents the outcome of evaluating a condition field."""

    passed: bool
    resolved: str
    error: str | None = None


_COMMENT_PATTERN = re.compile(r"(--|/\*)")
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(ATTACH|DETACH|ALTER|ANALYZE|CREATE|DROP|INSERT|UPDATE|DELETE|REPLACE|"
    r"VACUUM|TRIGGER|TABLE|INDEX|VIEW|PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|WITH)\b",
    re.IGNORECASE,
)


def _error_message(message: str, params: dict[str, str] | None = None) -> str:
    """Safely build an error message even if Django apps are not ready."""

    params = params or {}
    try:
        translated = _(message)
        return translated % params if params else translated
    except AppRegistryNotReady:
        return message % params if params else message


def _tokenize_condition(expression: str) -> list[str]:
    """Split a condition expression into SQL-style tokens."""

    tokens: list[str] = []
    length = len(expression)
    index = 0
    while index < length:
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        if char == "'":
            start = index
            index += 1
            while index < length:
                if expression[index] == "'":
                    if index + 1 < length and expression[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise ConditionEvaluationError(
                    _error_message("Unterminated string literal in condition.")
                )
            tokens.append(expression[start:index])
            continue
        two_char = expression[index : index + 2]
        if two_char in {"<=", ">=", "!=", "<>"}:
            tokens.append(two_char)
            index += 2
            continue
        if char in "=<>(),+-*/%":
            tokens.append(char)
            index += 1
            continue
        if char.isdigit():
            start = index
            has_decimal = False
            while index < length:
                current = expression[index]
                if current.isdigit():
                    index += 1
                    continue
                if current == "." and not has_decimal:
                    has_decimal = True
                    index += 1
                    continue
                break
            if index < length and expression[index] in {"e", "E"}:
                exp_index = index + 1
                if exp_index < length and expression[exp_index] in {"+", "-"}:
                    exp_index += 1
                while exp_index < length and expression[exp_index].isdigit():
                    exp_index += 1
                if exp_index == index + 1 or (
                    expression[index + 1] in {"+", "-"} and exp_index == index + 2
                ):
                    raise ConditionEvaluationError(
                        _error_message("Invalid numeric literal in condition.")
                    )
                index = exp_index
            tokens.append(expression[start:index])
            continue
        if char.isalpha() or char == "_":
            start = index
            while index < length and (
                expression[index].isalnum() or expression[index] in {"_", "."}
            ):
                index += 1
            tokens.append(expression[start:index])
            continue
        raise ConditionEvaluationError(
            _error_message(
                "Unsupported character %(character)r in condition.",
                {"character": char},
            )
        )
    return tokens


def _convert_tokens_to_python(tokens: list[str]) -> list[str]:
    """Map SQL-like tokens to their Python equivalents."""

    python_tokens: list[str] = []
    index = 0
    length = len(tokens)
    while index < length:
        token = tokens[index]
        upper = token.upper()
        if token.startswith("'"):
            python_tokens.append(token)
        elif token[0].isdigit() or (
            token[0] in {"+", "-"} and token[1:].replace('.', '', 1).isdigit()
        ):
            python_tokens.append(token)
        elif token in {"+", "-", "*", "/", "%", "(", ")", ","}:
            python_tokens.append(token)
        elif token in {"<=", ">=", "!=", "<>"}:
            python_tokens.append("!=" if token == "<>" else token)
        elif token == "=":
            python_tokens.append("==")
        elif upper == "AND":
            python_tokens.append("and")
        elif upper == "OR":
            python_tokens.append("or")
        elif upper == "NOT":
            if index + 1 < length and tokens[index + 1].upper() == "IN":
                python_tokens.append("not")
                python_tokens.append("in")
                index += 2
                continue
            python_tokens.append("not")
        elif upper == "IS":
            if index + 1 < length and tokens[index + 1].upper() == "NOT":
                python_tokens.append("is")
                python_tokens.append("not")
                index += 2
                continue
            python_tokens.append("is")
        elif upper == "IN":
            python_tokens.append("in")
        elif upper == "NULL":
            python_tokens.append("None")
        elif upper == "TRUE":
            python_tokens.append("True")
        elif upper == "FALSE":
            python_tokens.append("False")
        else:
            raise ConditionEvaluationError(
                _error_message(
                    "Unsupported token in condition: %(token)s",
                    {"token": token},
                )
            )
        index += 1
    return python_tokens


_ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Tuple,
    ast.List,
    ast.And,
    ast.Or,
    ast.Not,
    ast.UAdd,
    ast.USub,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    ast.Pow,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
)

_ALLOWED_BOOL_OPS = (ast.And, ast.Or)
_ALLOWED_UNARY_OPS = (ast.Not, ast.USub, ast.UAdd)
_ALLOWED_BIN_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    ast.Pow,
)
_ALLOWED_CMP_OPS = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
)
_ALLOWED_NAMES = {"True", "False", "None"}


def _validate_condition_ast(tree: ast.AST) -> None:
    """Ensure the compiled AST only contains safe operations."""

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ConditionEvaluationError(
                _error_message("Unsupported expression in condition.")
            )
        if isinstance(node, ast.BoolOp) and not isinstance(node.op, _ALLOWED_BOOL_OPS):
            raise ConditionEvaluationError(
                _error_message("Unsupported boolean operator in condition.")
            )
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, _ALLOWED_UNARY_OPS):
            raise ConditionEvaluationError(
                _error_message("Unsupported unary operator in condition.")
            )
        if isinstance(node, ast.BinOp) and not isinstance(node.op, _ALLOWED_BIN_OPS):
            raise ConditionEvaluationError(
                _error_message("Unsupported arithmetic operator in condition.")
            )
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, _ALLOWED_CMP_OPS):
                    raise ConditionEvaluationError(
                        _error_message("Unsupported comparison operator in condition.")
                    )
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ConditionEvaluationError(
                _error_message(
                    "Unknown identifier in condition: %(name)s",
                    {"name": node.id},
                )
            )


def _evaluate_sql_condition(expression: str) -> bool:
    """Evaluate a condition expression without constructing raw SQL."""

    if ";" in expression:
        raise ConditionEvaluationError(
            _error_message("Semicolons are not allowed in conditions."),
        )
    if _COMMENT_PATTERN.search(expression):
        raise ConditionEvaluationError(
            _error_message("SQL comments are not allowed in conditions."),
        )
    match = _FORBIDDEN_KEYWORDS.search(expression)
    if match:
        raise ConditionEvaluationError(
            _error_message(
                "Disallowed keyword in condition: %(keyword)s",
                {"keyword": match.group(1)},
            ),
        )

    tokens = _tokenize_condition(expression)
    python_tokens = _convert_tokens_to_python(tokens)
    python_expression = " ".join(python_tokens)

    try:
        tree = ast.parse(python_expression, mode="eval")
    except SyntaxError as exc:
        raise ConditionEvaluationError(
            _error_message("Invalid condition expression.")
        ) from exc

    _validate_condition_ast(tree)

    try:
        result = eval(compile(tree, "<condition>", "eval"), {}, {})
    except Exception as exc:  # pragma: no cover - runtime errors surface to the user
        raise ConditionEvaluationError(str(exc)) from exc

    if isinstance(result, bool):
        return result
    return bool(result)


class ConditionTextField(models.TextField):
    """Field storing a conditional SQL expression resolved through [sigils]."""

    def evaluate(self, instance) -> ConditionCheckResult:
        """Evaluate the stored expression for ``instance``."""

        value = self.value_from_object(instance)
        if hasattr(instance, "resolve_sigils"):
            resolved = instance.resolve_sigils(self.name)
        else:
            resolved = value

        if resolved is None:
            resolved_text = ""
        else:
            resolved_text = str(resolved)

        resolved_text = resolved_text.strip()
        if not resolved_text:
            return ConditionCheckResult(True, resolved_text)

        try:
            passed = _evaluate_sql_condition(resolved_text)
            return ConditionCheckResult(passed, resolved_text)
        except ConditionEvaluationError as exc:
            return ConditionCheckResult(False, resolved_text, str(exc))
