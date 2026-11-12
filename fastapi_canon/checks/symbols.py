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


def _scopes(tree: ast.Module, source: str, filename: str) -> dict[int, _LexicalScope]:
    tables = [symtable.symtable(source, filename, "exec")]
    functions = {}
    for table in tables:
        tables.extend(table.get_children())
        if isinstance(table, symtable.Function):
            functions[table.get_name(), table.get_lineno()] = _LexicalScope(
                frozenset(table.get_locals()),
                frozenset(table.get_frees()),
                frozenset(
                    symbol.get_name()
                    for symbol in table.get_symbols()
                    if symbol.is_assigned() or symbol.is_imported()
                ),
            )
    return {
        id(node): functions[node.name, node.lineno]
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and (node.name, node.lineno) in functions
    }
