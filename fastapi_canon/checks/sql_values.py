"""Bounded ORM value shapes; database exception bodies are outside the scan."""

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from fastapi_canon.checks.library_types import _LibraryTypes
from fastapi_canon.checks.symbols import _Definition
from fastapi_canon.checks.value_flow import _join, _Value

_SELECTS = {
    "sqlmodel.sql._expression_select_gen.select": "sqlmodel.sql._expression_select_cls.SelectOfScalar",
    "sqlalchemy.sql._selectable_constructors.select": "sqlalchemy.sql.selectable.Select",
}
_SESSIONS = {
    "sqlmodel.ext.asyncio.session.AsyncSession": True,
    "sqlmodel.orm.session.Session": False,
    "sqlalchemy.ext.asyncio.session.AsyncSession": True,
    "sqlalchemy.orm.session.Session": False,
}
_RESULTS = {
    f"sqlalchemy.{module}.{name}": asynchronous
    for module, names, asynchronous in (
        ("engine.result", ("Result", "ScalarResult", "MappingResult"), False),
        (
            "ext.asyncio.result",
            ("AsyncResult", "AsyncScalarResult", "AsyncMappingResult"),
            True,
        ),
    )
    for name in names
}


class _SqlSource(Protocol):
    libraries: _LibraryTypes

    @property
    def definitions(self) -> Mapping[str, _Definition]: ...
    def external(self, name: str) -> bool: ...
    def infer(self, node: ast.expr, module: str, env: dict[str, _Value]) -> _Value: ...
    def object(self, value: _Value) -> _Value: ...
    def resolve(self, node: ast.expr, module: str) -> str | None: ...
    def lineage(self, name: str) -> tuple[list[str], bool] | None: ...


@dataclass(frozen=True)
class _SqlReturn:
    value: _Value
    uncertain: bool = False


class _SqlValues:
    def __init__(self, source: _SqlSource) -> None:
        self.source = source

    def called(
        self,
        node: ast.Call,
        target: _Value,
        module: str,
        env: dict[str, _Value],
        *,
        awaited: bool,
    ) -> _SqlReturn | None:
        if not target.name.startswith(
            ("sqlmodel.", "sqlalchemy.")
        ) or not self.source.external(target.name):
            return None
        name = self.source.libraries.canonical(target.name)
        owner, _, method = name.rpartition(".")
        receiver = self.source.object(target.bound) if target.bound else _Value()
        receiver_name = self.source.libraries.canonical(receiver.name)
        if name in _SELECTS and not awaited:
            entities = tuple(
                self._entity(self.source.infer(arg, module, env)) for arg in node.args
            )
            row = _Value(
                "builtins.tuple", instance=True, exact=True, arguments=entities
            )
            scalar_selector = name.startswith("sqlmodel.") and len(entities) == 1
            return _SqlReturn(
                _Value(
                    _SELECTS[name]
                    if scalar_selector
                    else "sqlalchemy.sql.selectable.Select",
                    instance=True,
                    exact=True,
                    arguments=(entities[0] if scalar_selector else row,),
                ),
                bool(node.keywords)
                or any(self._custom_columns(entity.name) for entity in entities),
            )
        if owner in _SESSIONS and awaited == _SESSIONS[owner]:
            argument = (
                node.args[0]
                if node.args
                else next(
                    (
                        kw.value
                        for kw in node.keywords
                        if kw.arg == ("entity" if method == "get" else "statement")
                    ),
                    None,
                )
            )
            value = (
                self.source.infer(argument, module, env)
                if argument is not None
                else _Value()
            )
            if method == "get":
                entity = self._entity(value)
                return _SqlReturn(
                    _join(
                        entity, _Value("builtins.NoneType", instance=True, exact=True)
                    ),
                    self._custom_columns(entity.name),
                )
            if method in {"exec", "execute", "scalars"}:
                query = self.source.object(value)
                if not query.arguments:
                    return None
                query_name = self.source.libraries.canonical(query.name)
                if query_name not in {
                    "sqlmodel.sql._expression_select_cls.SelectOfScalar",
                    "sqlalchemy.sql.selectable.Select",
                }:
                    return None
                row = query.arguments[0]
                scalar_result = method == "scalars" or (
                    method == "exec" and query_name.endswith(".SelectOfScalar")
                )
                if scalar_result and row.name == "builtins.tuple":
                    row = row.arguments[0] if row.arguments else _Value()
                if not scalar_result and query_name.endswith(".SelectOfScalar"):
                    row = _Value(
                        "builtins.tuple", instance=True, arguments=(row,), exact=True
                    )
                return _SqlReturn(
                    _Value(
                        "sqlalchemy.engine.result.ScalarResult"
                        if scalar_result
                        else "sqlalchemy.engine.result.Result",
                        instance=True,
                        exact=True,
                        arguments=(row,),
                    ),
                    query.unresolved,
                )
        if receiver_name in _RESULTS and receiver.arguments:
            asynchronous = _RESULTS[receiver_name]
            row = receiver.arguments[0]
            scalar = (
                row.arguments[0]
                if row.name == "builtins.tuple" and row.arguments
                else row
                if receiver_name.endswith("ScalarResult")
                else _Value()
            )
            if method in {"unique", "yield_per"} and not awaited:
                return _SqlReturn(receiver, receiver.unresolved)
            if method == "scalars" and not awaited:
                index = (
                    node.args[0]
                    if node.args
                    else next(
                        (kw.value for kw in node.keywords if kw.arg == "index"), None
                    )
                )
                if index is not None and not (
                    isinstance(index, ast.Constant) and index.value == 0
                ):
                    scalar = _Value()
                return _SqlReturn(
                    _Value(
                        "sqlalchemy.ext.asyncio.result.AsyncScalarResult"
                        if asynchronous
                        else "sqlalchemy.engine.result.ScalarResult",
                        instance=True,
                        exact=True,
                        arguments=(scalar,),
                    ),
                    receiver.unresolved,
                )
            if awaited == asynchronous:
                if method in {"one", "scalar_one"}:
                    return _SqlReturn(
                        scalar if method == "scalar_one" else row, receiver.unresolved
                    )
                if method in {"one_or_none", "first", "scalar", "scalar_one_or_none"}:
                    result = (
                        scalar if method in {"scalar", "scalar_one_or_none"} else row
                    )
                    return _SqlReturn(
                        _join(
                            result,
                            _Value("builtins.NoneType", instance=True, exact=True),
                        ),
                        receiver.unresolved,
                    )
                if method in {"all", "fetchall", "fetchmany"}:
                    return _SqlReturn(
                        _Value(
                            "builtins.list", instance=True, exact=True, arguments=(row,)
                        ),
                        receiver.unresolved,
                    )
        return None

    def _entity(self, value: _Value) -> _Value:
        if value.instance or not value.name or value.name.startswith("<"):
            return _Value()
        definition = self.source.definitions.get(value.name)
        return (
            _Value(value.name, instance=True)
            if definition and isinstance(definition.node, ast.ClassDef)
            else _Value()
        )

    def _custom_columns(self, name: str) -> bool:
        if not name:
            return False
        for owner in (self.source.lineage(name) or ([name], False))[0]:
            definition = self.source.definitions.get(owner)
            if definition is None or not isinstance(definition.node, ast.ClassDef):
                continue
            for statement in definition.node.body:
                if (
                    not isinstance(statement, ast.Assign | ast.AnnAssign)
                    or statement.value is None
                ):
                    continue
                metadata: list[ast.expr] = []
                for node in ast.walk(statement.value):
                    if isinstance(node, ast.keyword) and node.arg in {
                        "sa_type",
                        "sa_column",
                        "type_",
                    }:
                        metadata.append(node.value)
                    if isinstance(node, ast.Call):
                        constructor = (
                            self.source.resolve(node.func, definition.module) or ""
                        )
                        if constructor.startswith("sqlalchemy.") and constructor.rsplit(
                            ".", 1
                        )[-1] in {"Column", "mapped_column"}:
                            metadata.extend(node.args)
                for value in metadata:
                    for child in ast.walk(value):
                        if (
                            isinstance(child, ast.Name | ast.Attribute)
                            and self.source.resolve(child, definition.module)
                            in self.source.definitions
                        ):
                            return True
        return False
