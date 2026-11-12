"""Python's lexical binding rules, without importing or executing source."""

import ast
import symtable
from dataclasses import dataclass


@dataclass
class _Definition:
    name: str
    module: str
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    owner: str | None = None


@dataclass(frozen=True)
class _LexicalScope:
    local_names: frozenset[str]
    free_names: frozenset[str]
    assigned_names: frozenset[str]


def _import_condition(node: ast.expr, module: str) -> bool | None:
    """Evaluate only unshadowed module-name guards under source import semantics."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _import_condition(node.operand, module)
        return not inner if inner is not None else None
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    first, second = node.left, node.comparators[0]
    if isinstance(second, ast.Name) and second.id == "__name__":
        first, second = second, first
    if not (
        isinstance(first, ast.Name)
        and first.id == "__name__"
        and isinstance(second, ast.Constant)
        and isinstance(second.value, str)
    ):
        return None
    if isinstance(node.ops[0], ast.Eq):
        return module == second.value
    if isinstance(node.ops[0], ast.NotEq):
        return module != second.value
    return None


def _scopes(tree: ast.Module, source: str, filename: str) -> dict[int, _LexicalScope]:
    tables = [symtable.symtable(source, filename, "exec")]
    functions: dict[tuple[str, int], _LexicalScope] = {}
    for table in tables:
        tables.extend(table.get_children())
        if isinstance(table, symtable.Function):
            key = table.get_name(), table.get_lineno()
            previous = functions.get(
                key, _LexicalScope(frozenset(), frozenset(), frozenset())
            )
            functions[key] = _LexicalScope(
                previous.local_names | frozenset(table.get_locals()),
                previous.free_names | frozenset(table.get_frees()),
                previous.assigned_names
                | frozenset(
                    symbol.get_name()
                    for symbol in table.get_symbols()
                    if symbol.is_assigned() or symbol.is_imported()
                ),
            )
    return {
        id(node): functions[
            "lambda" if isinstance(node, ast.Lambda) else node.name, node.lineno
        ]
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
        and ("lambda" if isinstance(node, ast.Lambda) else node.name, node.lineno)
        in functions
    }


def _stable_captures(
    parent: ast.AST,
    definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    names: frozenset[str],
) -> bool:
    """Snapshot only cells that cannot change after this closure is created."""
    position = definition.lineno, definition.col_offset
    for node in ast.walk(parent):
        if isinstance(node, ast.Nonlocal) and names.intersection(node.names):
            return False
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and node.id in names
            and (node.lineno, node.col_offset) > position
        ):
            return False
        if isinstance(
            node,
            ast.For
            | ast.AsyncFor
            | ast.While
            | ast.ListComp
            | ast.SetComp
            | ast.DictComp
            | ast.GeneratorExp,
        ):
            children = list(ast.walk(node))
            contains_definition = any(
                isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
                and (child.lineno, child.col_offset) == position
                for child in children
            )
            if contains_definition and any(
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Store | ast.Del)
                and child.id in names
                for child in children
            ):
                return False
    return True
