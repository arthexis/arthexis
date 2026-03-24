"""Shared validation helpers for desktop shortcut expressions and URLs."""

from __future__ import annotations

import ast

_ALLOWED_CONDITION_AST_NODES = (
    ast.And,
    ast.BoolOp,
    ast.Call,
    ast.Compare,
    ast.Constant,
    ast.Eq,
    ast.Expression,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.List,
    ast.Load,
    ast.Lt,
    ast.LtE,
    ast.Name,
    ast.Not,
    ast.NotEq,
    ast.NotIn,
    ast.Or,
    ast.Set,
    ast.Tuple,
    ast.UnaryOp,
)

_ALLOWED_CONDITION_NAMES = {
    "group_names",
    "has_desktop_ui",
    "has_feature",
    "is_staff",
    "is_superuser",
}

_ALLOWED_URL_SCHEMES = {"http", "https"}


def build_ast_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return a mapping of AST nodes to their direct parent.

    Parameters:
        tree: Parsed AST tree that should be traversed.

    Returns:
        A dictionary mapping each child node to its direct parent node.
    """

    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _is_has_feature_callable_name(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether ``node`` is the callable name in a ``has_feature(...)`` call.

    Parameters:
        node: The AST name node under inspection.
        parents: Mapping of child nodes to their direct parent node.

    Returns:
        ``True`` when the name is used as the function target for a call.
    """

    parent = parents.get(node)
    return isinstance(parent, ast.Call) and parent.func is node
