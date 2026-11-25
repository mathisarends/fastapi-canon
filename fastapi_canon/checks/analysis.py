from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import NamedTuple

from fastapi_canon.checks.source import (
    CONTEXT_MANAGERS,
    LIBRARY_VALUE,
    SCOPED_CALLS,
    SourceDefinition,
    SourceIndex,
    SymbolEnvironment,
    SymbolValue,
)

_PLAIN_DECORATORS = frozenset(
    {
        "abc.abstractmethod",
        "builtins.classmethod",
        "builtins.property",
        "builtins.staticmethod",
        "functools.cached_property",
    }
)
_CONTEXT_MANAGER_DECORATORS = frozenset(
    {"contextlib.contextmanager", "contextlib.asynccontextmanager"}
)
_BROAD_EXCEPTIONS = frozenset({"builtins.Exception", "builtins.BaseException"})
_LIBRARY_HOOKS = ("__post_init__", "__attrs_post_init__", "model_post_init")
_FIXED_POINT_LIMIT = 20
_SHOWN_CANDIDATES = 3


@dataclass(frozen=True, slots=True)
class _Escape:
    exception: str
    trace: tuple[str, ...]
    line: int
    deferred: bool = False
    """Raised after the creating call returns, outside its handlers."""


@dataclass(frozen=True, slots=True)
class UnknownValue:
    message: str
    function: str
    line: int


class ExceptionFlow:
    """Accumulate one analysis path without sharing writable buffers with callers."""

    def __init__(
        self,
        escapes: Iterable[_Escape] = (),
        unknowns: Iterable[UnknownValue] = (),
    ) -> None:
        self._escapes = list(escapes)
        self._unknowns = list(unknowns)

    @property
    def escapes(self) -> tuple[_Escape, ...]:
        return tuple(self._escapes)

    @property
    def unknowns(self) -> tuple[UnknownValue, ...]:
        return tuple(self._unknowns)

    def extend(self, other: ExceptionFlow) -> None:
        self._escapes.extend(other.escapes)
        self._unknowns.extend(other.unknowns)

    def add_escape(self, escape: _Escape) -> None:
        self._escapes.append(escape)

    def add_escapes(self, escapes: Iterable[_Escape]) -> None:
        self._escapes.extend(escapes)

    def add_unknown(self, unknown: UnknownValue) -> None:
        self._unknowns.append(unknown)

    def add_unknowns(self, unknowns: Iterable[UnknownValue]) -> None:
        self._unknowns.extend(unknowns)

    def retain_escapes(self, predicate: Callable[[_Escape], bool]) -> None:
        self._escapes = [escape for escape in self._escapes if predicate(escape)]

    def defer(self) -> None:
        self._escapes = [replace(escape, deferred=True) for escape in self._escapes]

    def deduplicate(self) -> None:
        # Keep the first of the shortest paths, which recursion cannot lengthen.
        first: dict[tuple[str, bool], _Escape] = {}
        for item in self._escapes:
            known = first.get(_signature(item))
            if known is None or len(item.trace) < len(known.trace):
                first[_signature(item)] = item
        self._escapes = list(first.values())
        self._unknowns = list(dict.fromkeys(self._unknowns))


class ContractAnalyzer:
    """Over-approximate the application exceptions that can escape a function.

    Only a ``raise`` of an application exception and a call that reaches
    application code can add an escape. Constructs without a model are
    traversed generically; constructs that could only remove escapes, such as
    library handlers and context managers, are assumed not to remove them.
    """

    def __init__(self, source: SourceIndex) -> None:
        self.source = source
        self._summaries: dict[_SummaryKey, _Summary] = {}
        self._partial: dict[str, tuple[_Escape, ...]] = {}
        self._pending: list[set[str]] = []
        self._declarations: dict[int, dict[str, ast.expr]] = {}
        # Types that nested functions see in their enclosing scope.
        self._closures: dict[str, SymbolEnvironment] = {}

    def function(
        self,
        function: SourceDefinition,
        stack: tuple[str, ...] = (),
        *,
        iterated: bool = False,
        receiver: str | None = None,
        dependency_transport: str | None = None,
        callables: _Callables = (),
    ) -> ExceptionFlow:
        """Summarize *function*; iterated generators execute their bodies.

        *callables* bind parameters to the application callables a caller
        passes. A recursive call contributes the escapes computed so far for
        its target, which is analyzed again until its escapes are stable.
        """
        if function.name in stack:
            if self._pending:
                self._pending[-1].add(function.name)
            return ExceptionFlow(self._partial.get(function.name, ()))
        summary_key = _SummaryKey(
            function.name, iterated, receiver, dependency_transport, callables
        )
        if summary_key in self._summaries:
            escapes, unknowns, modules = self._summaries[summary_key]
            self.source.record_usage(modules)
            return ExceptionFlow(list(escapes), list(unknowns))
        with self.source.track_usage():
            flow, pending = self._fixed_point(
                function,
                stack,
                iterated=iterated,
                receiver=receiver,
                dependency_transport=dependency_transport,
                callables=callables,
            )
            # A result that used a partial summary of an active caller is
            # recomputed when that caller iterates.
            if self._pending:
                self._pending[-1].update(pending)
            if not pending:
                self._summaries[summary_key] = _Summary(
                    tuple(flow.escapes),
                    tuple(flow.unknowns),
                    frozenset(self.source.touched),
                )
            return flow

    def expression(
        self,
        node: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
        awaited: bool = False,
        iterated: bool = False,
    ) -> ExceptionFlow:
        if isinstance(node, ast.Await):
            return self.expression(
                node.value, function, env, stack=stack, awaited=True, iterated=iterated
            )
        if isinstance(node, ast.Lambda):
            return ExceptionFlow()
        if isinstance(node, ast.GeneratorExp) and not iterated:
            # The body runs whenever a consumer advances the generator.
            deferred = self._comprehension(node, function, env, stack=stack)
            deferred.defer()
            return deferred
        if isinstance(
            node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
        ):
            return self._comprehension(node, function, env, stack=stack)
        if isinstance(node, ast.Call):
            return self._invocation(
                node, function, env, stack=stack, awaited=awaited, iterated=iterated
            )
        flow = ExceptionFlow()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                flow.extend(self.expression(child, function, env, stack=stack))
        if isinstance(node, ast.NamedExpr):
            env[node.target.id] = self.source.infer(node.value, function.module, env)
        if isinstance(node, ast.Attribute):
            flow.extend(self._property(node, function, env, stack=stack))
        return flow

    def block(
        self,
        body: list[ast.stmt],
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
        reraised: tuple[_Escape, ...] | None = None,
        reraised_name: str | None = None,
    ) -> ExceptionFlow:
        """Analyze *body*; inside a handler, *reraised* holds what it caught."""
        flow = ExceptionFlow()
        scope = _Scope(stack, reraised, reraised_name)
        for node in body:
            if isinstance(node, ast.Raise):
                flow.extend(self._raise(node, function, env, scope=scope))
                break
            if isinstance(node, ast.Return):
                if node.value:
                    flow.extend(self.expression(node.value, function, env, stack=stack))
                break
            if isinstance(node, ast.If):
                flow.extend(self.expression(node.test, function, env, stack=stack))
                condition = self.source.condition(node.test, function.module, env)
                if condition is not False:
                    narrowed = self._narrowed(node.test, function, env)
                    flow.extend(
                        self._branch(node.body, function, narrowed, scope=scope)
                    )
                if condition is not True:
                    narrowed = self._narrowed(node.test, function, env, negated=True)
                    flow.extend(
                        self._branch(node.orelse, function, narrowed, scope=scope)
                    )
                self._invalidate(node, env)
                if _exits(node.body) and not node.orelse:
                    # Only a failed test continues after a guard that exits.
                    env.update(self._narrowed(node.test, function, env, negated=True))
                selected = node.body if condition else node.orelse
                if condition is not None and any(
                    isinstance(statement, ast.Return | ast.Raise)
                    for statement in selected
                ):
                    break
                continue
            flow.extend(self._statement(node, function, env, scope=scope))
        return flow

    def _fixed_point(
        self,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        iterated: bool,
        receiver: str | None,
        dependency_transport: str | None,
        callables: _Callables,
    ) -> tuple[ExceptionFlow, set[str]]:
        # The escape sets are finite and grow monotonically with the partial
        # summary, so iteration stops once a pass adds no exception.
        flow = ExceptionFlow()
        pending: set[str] = set()
        for _ in range(_FIXED_POINT_LIMIT):
            self._pending.append(set())
            flow = self._analyze_function(
                function,
                stack,
                iterated=iterated,
                receiver=receiver,
                dependency_transport=dependency_transport,
                callables=callables,
            )
            previous = self._partial.get(function.name, ())
            flow.add_escapes(previous)
            # The report retains the first path per exception. Propagation and
            # catching depend on exception types, not duplicate paths to them.
            flow.deduplicate()
            pending = self._pending.pop()
            recursive = function.name in pending
            pending.discard(function.name)
            known = {_signature(item) for item in previous}
            if not recursive or {_signature(item) for item in flow.escapes} <= known:
                break
            self._partial[function.name] = flow.escapes
        else:
            flow.add_unknown(
                UnknownValue(
                    "recursive call did not converge",
                    function.name,
                    function.node.lineno,
                )
            )
        self._partial.pop(function.name, None)
        return flow, pending

    def _analyze_function(
        self,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        iterated: bool,
        receiver: str | None = None,
        dependency_transport: str | None = None,
        callables: _Callables = (),
    ) -> ExceptionFlow:
        node = function.node
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        flow = ExceptionFlow()
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = self.source.resolve(target, function.module)
            if name == "abc.abstractmethod":
                return ExceptionFlow(
                    unknowns=[
                        UnknownValue(
                            "abstract implementation", function.name, node.lineno
                        )
                    ]
                )
            # Library decorators are assumed to add and remove no application
            # exceptions; application decorators can wrap the call arbitrarily.
            if _operation_decorator(decorator) or (
                self.source.origin(target, function.module) is not None
            ):
                continue
            flow.add_unknown(
                UnknownValue("unresolved decorator", function.name, decorator.lineno)
            )
        env = {
            **self._closures.get(function.name, {}),
            **self.source.environment(function, receiver, dependency_transport),
            **dict(callables),
        }
        body = self.block(node.body, function, env, stack=(*stack, function.name))
        if not iterated and _generator(node):
            body.defer()
        flow.extend(body)
        return flow

    def _statement(
        self,
        node: ast.stmt,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
    ) -> ExceptionFlow:
        stack = scope.stack
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            return self._definition(node, function, env, scope=scope)
        if isinstance(node, ast.Try | ast.TryStar):
            flow, settled = self._try(node, function, env, scope=scope)
            self._invalidate(node, env)
            env.update(settled)
            return flow
        if isinstance(node, ast.With | ast.AsyncWith):
            flow = self._context_manager(node, function, env, scope=scope)
            self._invalidate(node, env)
            return flow
        if isinstance(node, ast.For | ast.AsyncFor | ast.While):
            # Iterations may run zero or more times, like independent branches.
            if isinstance(node, ast.While):
                flow = self.expression(node.test, function, env, stack=stack)
            else:
                flow = self._iteration(
                    node.iter,
                    function,
                    env,
                    stack=stack,
                    asynchronous=isinstance(node, ast.AsyncFor),
                )
                self._invalidate(node.target, env)
                self._bind_element(node.target, node.iter, function, env=env, local=env)
            flow.extend(self._branch(node.body, function, env, scope=scope))
            flow.extend(self._branch(node.orelse, function, env, scope=scope))
            self._invalidate(node, env)
            return flow
        if isinstance(node, ast.Assign | ast.AnnAssign):
            return self._assignment(node, function, env, stack=stack)
        if isinstance(node, ast.Expr):
            return self.expression(node.value, function, env, stack=stack)
        if isinstance(node, ast.Match):
            return self._match(node, function, env, scope=scope)
        if isinstance(node, ast.Import | ast.ImportFrom):
            for name, target in self.source.imports(node, function.module).items():
                env[name] = SymbolValue(target)
            return ExceptionFlow()
        # Any other statement is harmless syntax: evaluate its expressions,
        # analyze its blocks as possible branches and forget what it binds.
        flow = ExceptionFlow()
        for _, value in ast.iter_fields(node):
            items = value if isinstance(value, list) else [value]
            statements = [item for item in items if isinstance(item, ast.stmt)]
            if statements:
                flow.extend(self._branch(statements, function, env, scope=scope))
            for item in items:
                if not isinstance(item, ast.expr):
                    continue
                if isinstance(getattr(item, "ctx", None), ast.Store | ast.Del):
                    flow.extend(self._target(item, function, env, stack=stack))
                else:
                    flow.extend(self.expression(item, function, env, stack=stack))
        self._invalidate(node, env)
        return flow

    def _narrowed(
        self,
        test: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        negated: bool = False,
    ) -> SymbolEnvironment:
        # isinstance(value, Class) holds where its test passed, and where a
        # failed test of not isinstance(value, Class) continues.
        combined = ast.Or if negated else ast.And
        tests = (
            test.values
            if isinstance(test, ast.BoolOp) and isinstance(test.op, combined)
            else [test]
        )
        if negated:
            tests = [
                item.operand
                for item in tests
                if isinstance(item, ast.UnaryOp) and isinstance(item.op, ast.Not)
            ]
        narrowed = env
        for item in tests:
            if not (
                isinstance(item, ast.Call)
                and len(item.args) == 2
                and not item.keywords
                and isinstance(item.args[0], ast.Name | ast.Attribute)
                and self.source.resolve(item.func, function.module, env)
                == "builtins.isinstance"
            ):
                continue
            name = self.source.resolve(item.args[1], function.module, env)
            definition = self.source.definition(name or "")
            if name and (
                (definition is not None and isinstance(definition.node, ast.ClassDef))
                or self.source.library(name)
            ):
                narrowed = {
                    **narrowed,
                    self.source.key(item.args[0]): SymbolValue(name, instance=True),
                }
        return narrowed

    def _branch(
        self,
        body: list[ast.stmt],
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
    ) -> ExceptionFlow:
        return self.block(
            body,
            function,
            env.copy(),
            stack=scope.stack,
            reraised=scope.reraised,
            reraised_name=scope.reraised_name,
        )

    def _definition(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
    ) -> ExceptionFlow:
        # Function bodies run when called; a class body runs now.
        flow = ExceptionFlow()
        for expression in node.decorator_list:
            flow.extend(self.expression(expression, function, env, stack=scope.stack))
        if isinstance(node, ast.ClassDef):
            for expression in [*node.bases, *(item.value for item in node.keywords)]:
                flow.extend(
                    self.expression(expression, function, env, stack=scope.stack)
                )
            flow.extend(self._branch(node.body, function, env, scope=scope))
            env[node.name] = SymbolValue()
            return flow
        for expression in [
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value),
        ]:
            flow.extend(self.expression(expression, function, env, stack=scope.stack))
        local = self.source.local_definition(node)
        env[node.name] = SymbolValue(local.name if local else "")
        if local is not None:
            self._closures[local.name] = env.copy()
        return flow

    def _raise(
        self,
        node: ast.Raise,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        if node.exc is None:
            if scope.reraised is None:
                flow.add_unknown(
                    UnknownValue("unresolved re-raise", function.name, node.lineno)
                )
            else:
                flow.add_escapes(scope.reraised)
            return flow
        flow.extend(self.expression(node.exc, function, env, stack=scope.stack))
        if node.cause:
            flow.extend(self.expression(node.cause, function, env, stack=scope.stack))
        exception = node.exc
        if (
            isinstance(exception, ast.Name)
            and exception.id == scope.reraised_name
            and scope.reraised is not None
        ):
            flow.add_escapes(scope.reraised)
            return flow
        value = self.source.infer(exception, function.module, env)
        constructed = isinstance(exception, ast.Call) and (
            self.source.resolve(exception.func, function.module, env) == value.name
        )
        name = value.name
        if (
            name
            and not (value.instance and not constructed and name in _BROAD_EXCEPTIONS)
            and self.source.exception(name)
        ):
            flow.add_escape(_Escape(name, (function.name,), node.lineno))
        elif not (name and self.source.library(name)):
            # A library exception is not an application error.
            flow.add_unknown(
                UnknownValue(
                    f"unresolved raised exception: {ast.unparse(exception)}",
                    function.name,
                    node.lineno,
                )
            )
        return flow

    def _try(
        self,
        node: ast.Try | ast.TryStar,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
    ) -> tuple[ExceptionFlow, SymbolEnvironment]:
        """Analyze *node* and return the bindings known after it.

        When every handler exits, only a completed body continues, so the
        names it binds keep their types unless ``finally`` rebinds them.
        """
        flow = ExceptionFlow()
        completed = env.copy()
        attempted = self.block(
            node.body,
            function,
            completed,
            stack=scope.stack,
            reraised=scope.reraised,
            reraised_name=scope.reraised_name,
        )
        flow.add_unknowns(attempted.unknowns)
        remaining = list(attempted.escapes)
        for handler in node.handlers:
            types = (
                handler.type.elts
                if isinstance(handler.type, ast.Tuple)
                else [handler.type]
            )
            names = [
                name
                for item in types
                if item is not None
                and (name := self.source.resolve(item, function.module, env))
            ]
            # A handler catches an application exception only when its type is
            # in the exception's resolved MRO; otherwise the exception escapes.
            caught = [
                item
                for item in remaining
                if not item.deferred
                and (
                    handler.type is None
                    or any(
                        name in (self.source.mro(item.exception) or ())
                        for name in names
                    )
                )
            ]
            if isinstance(node, ast.Try):
                remaining = [item for item in remaining if item not in caught]
            handler_env = env.copy()
            if handler.name:
                caught_types = list(dict.fromkeys(item.exception for item in caught))
                bound = caught_types if caught_types else names
                handler_env[handler.name] = SymbolValue(
                    bound[0] if len(bound) == 1 else "", instance=True
                )
            # Implicit failures (e.g. int(value)) can reach handlers too.
            flow.extend(
                self.block(
                    handler.body,
                    function,
                    handler_env,
                    stack=scope.stack,
                    reraised=tuple(caught),
                    reraised_name=handler.name,
                )
            )
        flow.add_escapes(remaining)
        # A return in finally can only discard escapes; a raise there adds one.
        flow.extend(self._branch(node.orelse, function, completed, scope=scope))
        flow.extend(self._branch(node.finalbody, function, env, scope=scope))
        if not all(_exits(handler.body) for handler in node.handlers):
            return flow, {}
        rebound = env.copy()
        self._invalidate(ast.Module(body=node.finalbody, type_ignores=[]), rebound)
        settled = {
            key: value
            for key, value in completed.items()
            if env.get(key) != value and rebound.get(key) == env.get(key)
        }
        return flow, settled

    def _assignment(
        self,
        node: ast.Assign | ast.AnnAssign,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        if node.value is not None:
            flow.extend(self.expression(node.value, function, env, stack=stack))
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            flow.extend(self._target(target, function, env, stack=stack))
        inferred = SymbolValue()
        if isinstance(node, ast.AnnAssign):
            inferred = self.source.typed(node.annotation, function.module)
        if not inferred.name and node.value is not None:
            inferred = self.source.infer(node.value, function.module, env)
        # Every value is inferred before any target is bound.
        unpacked = [
            self.source.unpack(target, node.value, function.module, env=env)
            if isinstance(target, ast.Tuple | ast.List) and node.value is not None
            else None
            for target in targets
        ]
        for target, values in zip(targets, unpacked, strict=True):
            if isinstance(target, ast.Name | ast.Attribute):
                self._bind(target, inferred, function, env=env)
            elif values is not None:
                assert isinstance(target, ast.Tuple | ast.List)
                for element, value in zip(target.elts, values, strict=True):
                    if isinstance(element, ast.Name | ast.Attribute):
                        self._bind(element, value, function, env=env)
                    else:
                        self._invalidate(element, env)
            else:
                self._invalidate(target, env)
        return flow

    def _bind(
        self,
        target: ast.Name | ast.Attribute,
        value: SymbolValue,
        function: SourceDefinition,
        *,
        env: SymbolEnvironment,
    ) -> None:
        # An annotated local keeps its declared type in every assignment.
        if isinstance(target, ast.Name):
            declared = self._declared(function).get(target.id)
            if declared is not None:
                value = self.source.typed(declared, function.module)
        env[self.source.key(target)] = value

    def _declared(self, function: SourceDefinition) -> dict[str, ast.expr]:
        node = function.node
        if id(node) not in self._declarations:
            self._declarations[id(node)] = (
                {
                    child.target.id: child.annotation
                    for child in _executed_nodes(node.body)
                    if isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Name)
                }
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                else {}
            )
        return self._declarations[id(node)]

    def _match(
        self,
        node: ast.Match,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
    ) -> ExceptionFlow:
        flow = self.expression(node.subject, function, env, stack=scope.stack)
        captured: set[str] = set()
        for case in node.cases:
            local = env.copy()
            names = _pattern_names(case.pattern)
            captured.update(names)
            for name in names:
                local[name] = SymbolValue()
            if case.guard is not None:
                flow.extend(
                    self.expression(case.guard, function, local, stack=scope.stack)
                )
            flow.extend(
                self.block(
                    case.body,
                    function,
                    local,
                    stack=scope.stack,
                    reraised=scope.reraised,
                    reraised_name=scope.reraised_name,
                )
            )
        for name in captured:
            env[name] = SymbolValue()
        self._invalidate(node, env)
        return flow

    def _target(
        self,
        node: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        # Evaluate the expressions inside an assignment or deletion target.
        flow = ExceptionFlow()
        match node:
            case ast.Attribute(value=value):
                flow.extend(self.expression(value, function, env, stack=stack))
            case ast.Subscript(value=value, slice=index):
                flow.extend(self.expression(value, function, env, stack=stack))
                flow.extend(self.expression(index, function, env, stack=stack))
            case ast.Starred(value=value):
                flow.extend(self._target(value, function, env, stack=stack))
            case ast.Tuple(elts=elements) | ast.List(elts=elements):
                for element in elements:
                    flow.extend(self._target(element, function, env, stack=stack))
        return flow

    def _invocation(
        self,
        node: ast.Call,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        for argument in [*node.args, *(kw.value for kw in node.keywords)]:
            # The callee consumes a generator expression passed to it.
            flow.extend(
                self.expression(
                    argument,
                    function,
                    env,
                    stack=stack,
                    iterated=isinstance(argument, ast.GeneratorExp),
                )
            )
        attribute = self.source.literal_getattr(node, function.module, env)
        if attribute is not None:
            # getattr with a literal name reads that attribute.
            flow.extend(self._property(attribute, function, env, stack=stack))
            return flow
        read = self._computed_getattr(node, function, env, stack=stack)
        if read is not None:
            flow.extend(read)
            return flow
        if isinstance(node.func, ast.Attribute) and _super(node.func.value):
            flow.extend(
                self._super_call(
                    node, function, env, stack=stack, awaited=awaited, iterated=iterated
                )
            )
            return flow
        target = self.source.infer(node.func, function.module, env)
        if target.instance and target.name in SCOPED_CALLS and target.arguments:
            # Scoped[UseCase] calls the use case resolved in a fresh scope.
            target = SymbolValue(target.arguments[0], instance=True)
        name = target.name or self.source.resolve(node.func, function.module, env)
        definition = self.source.definition(name or "")
        if definition and isinstance(node.func, ast.Attribute | ast.Call):
            receiver_expression = (
                node.func.value if isinstance(node.func, ast.Attribute) else node.func
            )
            flow.extend(
                self.expression(receiver_expression, function, env, stack=stack)
            )
        if name and name.startswith("builtins.") and self.source.exception(name):
            return flow
        if definition and isinstance(definition.node, ast.ClassDef):
            if target.instance:
                flow.extend(
                    self._instance_call(
                        node,
                        definition,
                        function,
                        stack=stack,
                        awaited=awaited,
                        iterated=iterated,
                    )
                )
            else:
                flow.extend(
                    self._construct(definition, function, stack, line=node.lineno)
                )
        elif definition and definition.owner and self.source.abstract(definition):
            flow.extend(
                self._dispatch(
                    node,
                    definition,
                    function,
                    stack=stack,
                    awaited=awaited,
                    iterated=iterated,
                )
            )
        elif definition:
            flow.extend(self._lambdas(node, function, env, stack=stack))
            candidates = [(definition, target.receiver)]
            if definition.owner and isinstance(node.func, ast.Attribute):
                receiver = self.source.resolve(node.func.value, function.module, env)
                if (
                    receiver
                    and receiver != definition.owner
                    and not self.source.classmethod(definition)
                ):
                    # The receiver may be a subclass that overrides the method.
                    candidates.extend(self._overrides(receiver, node.func.attr))
            for candidate, receiver_type in dict(candidates).items():
                flow.extend(
                    self._call(
                        node,
                        candidate,
                        function,
                        stack=stack,
                        awaited=awaited,
                        iterated=iterated,
                        receiver=receiver_type,
                        transport=self._transport(node, candidate, function, env=env),
                        callables=self._callables(node, candidate, function, env=env),
                    )
                )
        else:
            flow.extend(
                self._library_call(
                    node, function, env, stack=stack, awaited=awaited, iterated=iterated
                )
            )
        return flow

    def _instance_call(
        self,
        node: ast.Call,
        definition: SourceDefinition,
        function: SourceDefinition,
        *,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        method = self.source.method(definition.name, "__call__")
        if method is None:
            return ExceptionFlow(
                unknowns=[
                    UnknownValue(
                        f"unresolved callable instance: {ast.unparse(node.func)}",
                        function.name,
                        node.lineno,
                    )
                ]
            )
        if self.source.abstract(method):
            return self._dispatch(
                node, method, function, stack=stack, awaited=awaited, iterated=iterated
            )
        return self._call(
            node,
            method,
            function,
            stack=stack,
            awaited=awaited,
            iterated=iterated,
            receiver=definition.name,
        )

    def _library_call(
        self,
        node: ast.Call,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        """Follow a call without a scanned target, or report its candidates.

        Library code raises no application exceptions of its own; it reaches
        them only through receivers and callables it is given.
        """
        flow = ExceptionFlow()
        func = node.func
        if isinstance(func, ast.Attribute):
            flow.extend(self.expression(func.value, function, env, stack=stack))
        elif isinstance(func, ast.Call) and (
            self.source.resolve(func.func, function.module, env) == "builtins.getattr"
        ):
            # Only calling the looked-up attribute is unresolved.
            for argument in func.args:
                flow.extend(self.expression(argument, function, env, stack=stack))
        elif isinstance(func, ast.Call):
            flow.extend(self.expression(func, function, env, stack=stack))
        library = self.source.origin(func, function.module, env) is not None
        if not library and isinstance(func, ast.Attribute):
            imported = self.source.resolve(func.value, function.module, env)
            # An import that is neither scanned nor locatable may be
            # application code outside the roots.
            unlocated = imported is not None and self.source.unlocated(imported)
        else:
            unlocated = False
        if not library and not unlocated and isinstance(func, ast.Attribute):
            owner = self._receiver_class(func.value, function, env)
            receiver = self.source.infer(func.value, function.module, env)
            method = self.source.method(owner, func.attr) if owner else None
            if (
                owner is not None
                and method is not None
                and isinstance(method.node, ast.FunctionDef | ast.AsyncFunctionDef)
                and not self.source.getter(method)
            ):
                # A receiver typed through a field, property or element: the
                # scanned method or an override in a subclass runs.
                flow.extend(
                    self._method_call(
                        node,
                        method,
                        owner,
                        function=function,
                        env=env,
                        stack=stack,
                        awaited=awaited,
                        iterated=iterated,
                    )
                )
                return flow
            if owner is not None and self.source.attribute(owner, func.attr):
                held = self._held(
                    node,
                    owner,
                    function=function,
                    stack=stack,
                    awaited=awaited,
                    iterated=iterated,
                )
                if held is None:
                    # An attribute that holds a callable of unknown origin.
                    flow.add_unknown(
                        UnknownValue(
                            f"unresolved call: {ast.unparse(func)}",
                            function.name,
                            node.lineno,
                        )
                    )
                else:
                    flow.extend(held)
                return flow
            if owner is not None:
                # No scanned class in the receiver's lineage defines the
                # method: a library base does, unless a subclass overrides it.
                for candidate, receiver_type in self._overrides(owner, func.attr):
                    flow.extend(
                        self._call(
                            node,
                            candidate,
                            function,
                            stack=stack,
                            awaited=awaited,
                            iterated=iterated,
                            receiver=receiver_type,
                        )
                    )
                flow.extend(self._hooks(owner, function, stack))
                library = True
            elif receiver.instance and self.source.library(receiver.name):
                library = True
            elif not (candidates := self._accepting(node, func.attr)):
                # No application member with the name accepts these arguments:
                # the call cannot reach application code, whatever the receiver is.
                library = True
            else:
                flow.add_unknown(
                    UnknownValue(
                        _ambiguous(f"call {ast.unparse(func)}", candidates),
                        function.name,
                        node.lineno,
                    )
                )
                return flow
        if not library:
            flow.add_unknown(
                UnknownValue(
                    f"unresolved call: {ast.unparse(func)}",
                    function.name,
                    node.lineno,
                )
            )
            return flow
        for argument in [*node.args, *(kw.value for kw in node.keywords)]:
            flow.extend(self._callback(argument, function, env, stack=stack))
        return flow

    def _accepting(self, node: ast.Call, name: str) -> list[str]:
        # Members named *name* that can be called with the arguments of
        # *node*; any other target raises TypeError before running.
        result = []
        for candidate in self.source.candidates(name):
            definition = self.source.definition(candidate)
            if definition is None:
                owner = candidate.rpartition(".")[0]
                field = self.source.fields(owner).get(name)
                held = self.source.definition(field.name) if field else None
                if (
                    field is not None
                    and field.instance
                    and held is not None
                    and isinstance(held.node, ast.ClassDef)
                    and self.source.method(held.name, "__call__") is None
                ):
                    continue
            elif not _accepts(definition, node, bound=not self._unbound(definition)):
                continue
            result.append(candidate)
        return result

    def _unbound(self, definition: SourceDefinition) -> bool:
        node = definition.node
        return definition.owner is None or (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(
                self.source.resolve(decorator, definition.module)
                == "builtins.staticmethod"
                for decorator in node.decorator_list
            )
        )

    def _computed_getattr(
        self,
        node: ast.Call,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow | None:
        # getattr(instance, name) of a known type can run any of its getters.
        if (
            self.source.resolve(node.func, function.module, env) != "builtins.getattr"
            or node.keywords
            or len(node.args) not in {2, 3}
        ):
            return None
        receiver = node.args[0]
        value = self.source.infer(receiver, function.module, env)
        if value.instance and self.source.library(value.name):
            return ExceptionFlow()
        owner = self._receiver_class(receiver, function, env)
        if owner is None or not value.instance:
            return None
        lineage = self.source.lineage(owner)
        classes = [
            *(lineage[0] if lineage else [owner]),
            *self.source.subclasses(owner),
        ]
        flow = ExceptionFlow()
        for name in dict.fromkeys(classes):
            definition = self.source.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for member in definition.node.body:
                getter = self.source.definitions.get(
                    f"{name}.{getattr(member, 'name', '')}"
                )
                if (
                    getter is None
                    or self.source.abstract(getter)
                    or not (
                        self.source.getter(getter)
                        or getter.node.name in {"__getattr__", "__getattribute__"}
                    )
                ):
                    continue
                flow.extend(
                    self._traced(function, self.function(getter, stack, receiver=name))
                )
        return flow

    def _method_call(
        self,
        node: ast.Call,
        method: SourceDefinition,
        owner: str,
        *,
        function: SourceDefinition,
        env: SymbolEnvironment,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        if self.source.abstract(method):
            return self._dispatch(
                node, method, function, stack=stack, awaited=awaited, iterated=iterated
            )
        assert isinstance(node.func, ast.Attribute)
        flow = self._lambdas(node, function, env, stack=stack)
        candidates = [(method, owner), *self._overrides(owner, node.func.attr)]
        for candidate, receiver_type in dict(candidates).items():
            flow.extend(
                self._call(
                    node,
                    candidate,
                    function,
                    stack=stack,
                    awaited=awaited,
                    iterated=iterated,
                    receiver=receiver_type,
                    transport=self._transport(node, candidate, function, env=env),
                    callables=self._callables(node, candidate, function, env=env),
                )
            )
        return flow

    def _receiver_class(
        self, node: ast.expr, function: SourceDefinition, env: SymbolEnvironment
    ) -> str | None:
        # The scanned class of an instance or class receiver, if known.
        value = self.source.infer(node, function.module, env)
        for name in (value.name, self.source.resolve(node, function.module, env)):
            definition = self.source.definition(name or "")
            if definition is not None and isinstance(definition.node, ast.ClassDef):
                return definition.name
        return None

    def _property(
        self,
        node: ast.Attribute,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        # Reading a property runs its getter, or an implementation's getter
        # when the property is abstract.
        name = self.source.resolve(node, function.module, env)
        member = self.source.definition(name or "")
        if member is None or not self.source.getter(member):
            return ExceptionFlow()
        flow = ExceptionFlow()
        if member.owner and self.source.abstract(member):
            candidates = self._implementations(member)
            fields = any(
                node.attr in self.source.fields(implementation)
                for implementation in self._implementing_classes(member.owner)
            )
            if not candidates and not fields:
                flow.add_unknown(
                    UnknownValue(
                        f"abstract method without scanned implementation: {member.name}",
                        function.name,
                        node.lineno,
                    )
                )
            for candidate, implementation in candidates.items():
                flow.extend(self.function(candidate, stack, receiver=implementation))
            return flow
        flow.extend(self.function(member, stack))
        receiver = self.source.resolve(node.value, function.module, env)
        if receiver and receiver != member.owner:
            # The receiver may be a subclass that overrides the property.
            for candidate, receiver_type in self._overrides(receiver, node.attr):
                flow.extend(self.function(candidate, stack, receiver=receiver_type))
        return flow

    def _overrides(
        self, owner: str, name: str
    ) -> list[tuple[SourceDefinition, str | None]]:
        inherited = self.source.method(owner, name)
        result: list[tuple[SourceDefinition, str | None]] = []
        for subclass in self.source.subclasses(owner):
            method = self.source.method(subclass, name)
            if (
                method is not None
                and method != inherited
                and all(method != item for item, _ in result)
            ):
                result.append((method, subclass))
        return result

    def _super_call(
        self,
        node: ast.Call,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        """Follow ``super().name(...)`` to the next scanned definition in the MRO."""
        assert isinstance(node.func, ast.Attribute)
        assert isinstance(node.func.value, ast.Call)
        call = node.func.value
        start = (
            self.source.resolve(call.args[0], function.module, env)
            if call.args
            else function.owner
        )
        function_node = function.node
        assert isinstance(function_node, ast.FunctionDef | ast.AsyncFunctionDef)
        positional = [*function_node.args.posonlyargs, *function_node.args.args]
        bound = env.get(positional[0].arg) if positional else None
        receiver = bound.name if bound is not None else ""
        receiver_order = self.source.mro(receiver) if receiver else None
        order = (
            receiver_order
            if receiver_order and start in receiver_order
            else self.source.mro(start or "")
        )
        if start is None or order is None or start not in order:
            return ExceptionFlow(
                unknowns=[
                    UnknownValue(
                        f"unresolved super() call: {ast.unparse(node.func)}",
                        function.name,
                        node.lineno,
                    )
                ]
            )
        following = order[order.index(start) + 1 :]
        method = next(
            (
                self.source.definitions[f"{base}.{node.func.attr}"]
                for base in following
                if f"{base}.{node.func.attr}" in self.source.definitions
            ),
            None,
        )
        if method is None:
            flow = ExceptionFlow()
            for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                flow.extend(self._callback(argument, function, env, stack=stack))
            return flow
        return self._call(
            node,
            method,
            function,
            stack=stack,
            awaited=awaited,
            iterated=iterated,
            receiver=receiver or None,
        )

    def _comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        local = env.copy()
        # All targets are lexical locals, even in filters before a later for.
        # The first iterable alone is evaluated in the enclosing environment.
        for generator in node.generators:
            self._invalidate(generator.target, local)
        for index, generator in enumerate(node.generators):
            scope = env if index == 0 else local
            flow.extend(
                self._iteration(
                    generator.iter,
                    function,
                    scope,
                    stack=stack,
                    asynchronous=bool(generator.is_async),
                )
            )
            self._bind_element(
                generator.target, generator.iter, function, env=scope, local=local
            )
            for condition in generator.ifs:
                flow.extend(self.expression(condition, function, local, stack=stack))
        values = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        for value in values:
            flow.extend(self.expression(value, function, local, stack=stack))
        return flow

    def _bind_element(
        self,
        target: ast.expr,
        iterable: ast.expr,
        function: SourceDefinition,
        *,
        env: SymbolEnvironment,
        local: SymbolEnvironment,
    ) -> None:
        # A loop target takes the element type of its annotated container.
        if not isinstance(target, ast.Name):
            return
        element = self.source.infer(iterable, function.module, env).element
        if element:
            local[target.id] = SymbolValue(element, instance=True)

    def _iteration(
        self,
        node: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
        asynchronous: bool,
    ) -> ExceptionFlow:
        """Evaluate an iterable and follow a scanned class's iteration protocol."""
        flow = self.expression(node, function, env, stack=stack, iterated=True)
        value = self.source.infer(node, function.module, env)
        definition = self.source.definition(value.name) if value.instance else None
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            return flow
        names = ("__aiter__", "__anext__") if asynchronous else ("__iter__", "__next__")
        for name in names:
            method = self.source.method(definition.name, name)
            if method is not None:
                flow.extend(
                    self._traced(
                        function,
                        self.function(
                            method, stack, iterated=True, receiver=definition.name
                        ),
                    )
                )
        return flow

    def _context_manager(
        self,
        node: ast.With | ast.AsyncWith,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        scope: _Scope,
        index: int = 0,
    ) -> ExceptionFlow:
        if index == len(node.items):
            return self.block(
                node.body,
                function,
                env,
                stack=scope.stack,
                reraised=scope.reraised,
                reraised_name=scope.reraised_name,
            )
        item = node.items[index]
        expression = item.context_expr
        flow = self.expression(expression, function, env, stack=scope.stack)
        local = env.copy()
        if item.optional_vars is not None:
            self._invalidate(item.optional_vars, local)
        suppressed = self._suppressed(expression, function, env)
        if suppressed is not None or self._managed_call(expression, function, env):
            # The protocol of an application generator manager runs with its
            # call; neither kind is assumed to suppress unresolved types.
            names = suppressed or []
            if isinstance(item.optional_vars, ast.Name) and suppressed is None:
                local[item.optional_vars.id] = self._yielded(expression, function, env)
            inner = self._context_manager(
                node, function, local, scope=scope, index=index + 1
            )
            inner.retain_escapes(
                lambda escape: (
                    escape.deferred
                    or not any(
                        name in (self.source.mro(escape.exception) or ())
                        for name in names
                    )
                )
            )
            flow.extend(inner)
            return flow
        manager = self._context_type(expression, function, env)
        asynchronous = isinstance(node, ast.AsyncWith)
        if manager is None:
            library = self.source.origin(expression, function.module, env) is not None
            value = self.source.infer(expression, function.module, env)
            entered = (
                value.arguments[0]
                if value.name in CONTEXT_MANAGERS and value.arguments
                else ""
            )
            protocol = (
                ("__aenter__", "__aexit__")
                if asynchronous
                else ("__enter__", "__exit__")
            )
            candidates = tuple(
                candidate
                for name in protocol
                for candidate in self.source.candidates(name)
            )
            if not library and candidates:
                flow.add_unknown(
                    UnknownValue(
                        _ambiguous(
                            f"context manager {ast.unparse(expression)}", candidates
                        ),
                        function.name,
                        expression.lineno,
                    )
                )
            if isinstance(item.optional_vars, ast.Name) and entered:
                # A typed manager enters its type argument.
                local[item.optional_vars.id] = SymbolValue(entered, instance=True)
            elif isinstance(item.optional_vars, ast.Name) and library:
                local[item.optional_vars.id] = (
                    value
                    if value.instance and self.source.library(value.name)
                    else SymbolValue(LIBRARY_VALUE, instance=True)
                )
            # A library manager is assumed not to suppress application errors.
            flow.extend(
                self._context_manager(
                    node, function, local, scope=scope, index=index + 1
                )
            )
            return flow
        enter = self.source.method(
            manager, "__aenter__" if asynchronous else "__enter__"
        )
        exit_method = self.source.method(
            manager, "__aexit__" if asynchronous else "__exit__"
        )
        if enter is not None:
            flow.extend(
                self._traced(
                    function, self.function(enter, scope.stack, receiver=manager)
                )
            )
            if _always_raises(enter.node):
                return flow
            enter_node = enter.node
            if isinstance(item.optional_vars, ast.Name) and isinstance(
                enter_node, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                returned = (
                    self.source.typed(enter_node.returns, enter.module)
                    if enter_node.returns
                    else SymbolValue()
                )
                if returned.name in {"typing.Self", "typing_extensions.Self"}:
                    returned = SymbolValue(manager, instance=True)
                local[item.optional_vars.id] = replace(returned, instance=True)
        inner = self._context_manager(
            node, function, local, scope=scope, index=index + 1
        )
        if exit_method is not None and (
            _suppression(exit_method.node) is True or _always_raises(exit_method.node)
        ):
            inner.retain_escapes(lambda escape: escape.deferred)
        flow.extend(inner)
        if exit_method is not None:
            flow.extend(
                self._traced(
                    function, self.function(exit_method, scope.stack, receiver=manager)
                )
            )
        return flow

    def _suppressed(
        self, node: ast.expr, function: SourceDefinition, env: SymbolEnvironment
    ) -> list[str] | None:
        # The exception types of a contextlib.suppress call; [] when unresolved.
        if not (
            isinstance(node, ast.Call)
            and self.source.resolve(node.func, function.module, env)
            == "contextlib.suppress"
        ):
            return None
        names = [
            self.source.resolve(argument, function.module, env)
            for argument in node.args
        ]
        if node.keywords or any(name is None for name in names):
            return []
        return [name for name in names if name is not None]

    def _yielded(
        self, node: ast.expr, function: SourceDefinition, env: SymbolEnvironment
    ) -> SymbolValue:
        # A generator manager enters the value its generator yields; a typed
        # manager, such as a port's AbstractAsyncContextManager[T], enters T.
        value = self.source.infer(node, function.module, env)
        if value.name in CONTEXT_MANAGERS and value.arguments and value.arguments[0]:
            return SymbolValue(value.arguments[0], instance=True)
        assert isinstance(node, ast.Call)
        elements = {
            self.source.element(definition.node.returns, definition.module)
            if isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
            and definition.node.returns
            else ""
            for definition in self._targets(node, function, env)
        }
        element = elements.pop() if len(elements) == 1 else ""
        return SymbolValue(element, instance=True) if element else SymbolValue()

    def _managed_call(
        self, node: ast.expr, function: SourceDefinition, env: SymbolEnvironment
    ) -> bool:
        if not isinstance(node, ast.Call):
            return False
        targets = self._targets(node, function, env)
        return bool(targets) and all(self._managed(item) for item in targets)

    def _targets(
        self, node: ast.Call, function: SourceDefinition, env: SymbolEnvironment
    ) -> list[SourceDefinition]:
        # The scanned functions a call runs: a function, or the __call__ of an
        # instance, dispatched to every implementation when it is abstract.
        target = self.source.infer(node.func, function.module, env)
        definition = self.source.definition(
            target.name or self.source.resolve(node.func, function.module, env) or ""
        )
        if definition is None:
            return []
        if isinstance(definition.node, ast.ClassDef):
            method = (
                self.source.method(definition.name, "__call__")
                if target.instance
                else None
            )
            if method is None:
                return []
            definition = method
        if definition.owner and self.source.abstract(definition):
            return list(self._implementations(definition))
        return [definition]

    def _call(
        self,
        node: ast.Call,
        definition: SourceDefinition,
        function: SourceDefinition,
        *,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
        receiver: str | None = None,
        transport: str | None = None,
        callables: _Callables = (),
    ) -> ExceptionFlow:
        # A generator-based context manager runs its body around the with block.
        iterated_generator = (iterated or self._managed(definition)) and _generator(
            definition.node
        )
        called = self._traced(
            function,
            self.function(
                definition,
                stack,
                iterated=iterated_generator,
                receiver=receiver,
                dependency_transport=transport,
                callables=callables,
            ),
        )
        if (
            isinstance(definition.node, ast.AsyncFunctionDef)
            and not awaited
            and not iterated_generator
        ):
            # An unawaited coroutine runs later, outside this function's handlers.
            called.defer()
        return called

    def _managed(self, definition: SourceDefinition) -> bool:
        # A generator function decorated with contextlib.(async)contextmanager.
        node = definition.node
        return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            self.source.resolve(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                definition.module,
            )
            in _CONTEXT_MANAGER_DECORATORS
            for decorator in node.decorator_list
        )

    def _context_type(
        self, node: ast.expr, function: SourceDefinition, env: SymbolEnvironment
    ) -> str | None:
        awaited = isinstance(node, ast.Await)
        expression = node.value if isinstance(node, ast.Await) else node
        target = expression.func if isinstance(expression, ast.Call) else expression
        resolved = self.source.resolve(target, function.module, env)
        definition = self.source.definition(resolved or "")
        if isinstance(expression, ast.Call) and definition:
            if isinstance(definition.node, ast.ClassDef):
                if self.source.infer(target, function.module, env).instance:
                    # Calling an instance: its __call__ returns the manager.
                    return None
                return definition.name if not awaited else None
            if (
                isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
                and isinstance(definition.node, ast.AsyncFunctionDef) == awaited
                and not _generator(definition.node)
                and definition.node.returns
            ):
                returned = self.source.annotation(
                    definition.node.returns, definition.module
                )
                result = self.source.definition(returned or "")
                if result and isinstance(result.node, ast.ClassDef):
                    return result.name
        elif (
            definition
            and isinstance(definition.node, ast.ClassDef)
            and self.source.key(expression) in env
        ):
            return definition.name
        return None

    def _construct(
        self,
        definition: SourceDefinition,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        line: int,
    ) -> ExceptionFlow:
        """Follow the application code that constructing *definition* runs."""
        flow = ExceptionFlow()
        lineage = self.source.lineage(definition.name)
        if lineage is None:
            flow.add_unknown(
                UnknownValue(
                    f"unresolved constructor: {definition.name}", function.name, line
                )
            )
            return flow
        classes, external = lineage
        node = definition.node
        assert isinstance(node, ast.ClassDef)
        changes = [
            decorator.func if isinstance(decorator, ast.Call) else decorator
            for decorator in node.decorator_list
        ]
        changes.extend(
            keyword.value for keyword in node.keywords if keyword.arg == "metaclass"
        )
        for change in changes:
            name = self.source.resolve(change, definition.module)
            if name is None or not self.source.external(name):
                flow.add_unknown(
                    UnknownValue(
                        "class decorators or metaclasses may change construction",
                        function.name,
                        line,
                    )
                )
                return flow
            external = True
        if not external and node.keywords:
            # Class keywords of a scanned hierarchy reach its own __init_subclass__.
            flow.add_unknown(
                UnknownValue(
                    "class decorators or metaclasses may change construction",
                    function.name,
                    line,
                )
            )
        for hook in ("__new__", "__init__"):
            member = next(
                (
                    self.source.definitions[f"{owner}.{hook}"]
                    for owner in classes
                    if f"{owner}.{hook}" in self.source.definitions
                ),
                None,
            )
            if member is not None:
                flow.extend(
                    self._traced(
                        function, self.function(member, stack, receiver=definition.name)
                    )
                )
        if external:
            flow.extend(self._hooks(definition.name, function, stack, library=True))
        return flow

    def _hooks(
        self,
        owner: str,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        library: bool = False,
    ) -> ExceptionFlow:
        """Follow the application hooks a library base can call on *owner*.

        These are post-init hooks and decorated methods such as Pydantic
        validators and serializers.
        """
        flow = ExceptionFlow()
        lineage = self.source.lineage(owner)
        if lineage is None or not (library or lineage[1]):
            return flow
        classes = lineage[0]
        hooks = [
            member
            for hook in _LIBRARY_HOOKS
            if (member := self.source.method(owner, hook)) is not None
        ]
        for name in classes:
            owner_node = self.source.definitions[name].node
            assert isinstance(owner_node, ast.ClassDef)
            module = self.source.definitions[name].module
            hooks.extend(
                self.source.definitions[f"{name}.{member.name}"]
                for member in owner_node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and any(
                    self.source.resolve(
                        item.func if isinstance(item, ast.Call) else item, module
                    )
                    not in _PLAIN_DECORATORS
                    for item in member.decorator_list
                )
            )
        for hook in dict.fromkeys(hooks):
            flow.extend(
                self._traced(function, self.function(hook, stack, receiver=owner))
            )
        return flow

    def _callback(
        self,
        argument: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        """Assume library code calls the application callables it receives."""
        if isinstance(argument, ast.Starred):
            argument = argument.value
        if isinstance(argument, ast.Lambda):
            lambda_env = dict(env)
            parameters = argument.args
            for item in [
                *parameters.posonlyargs,
                *parameters.args,
                *parameters.kwonlyargs,
                *filter(None, [parameters.vararg, parameters.kwarg]),
            ]:
                lambda_env[item.arg] = SymbolValue()
            return self.expression(argument.body, function, lambda_env, stack=stack)
        if not isinstance(argument, ast.Name | ast.Attribute | ast.Call):
            return ExceptionFlow()
        inferred = self.source.infer(argument, function.module, env)
        name = inferred.name or self.source.resolve(argument, function.module, env)
        definition = self.source.definition(name or "")
        if definition is None:
            return ExceptionFlow()
        if isinstance(definition.node, ast.ClassDef):
            # Typed parameters and attributes resolve to their class; they
            # pass an instance, not a constructor.
            if inferred.instance:
                method = self.source.method(definition.name, "__call__")
                if method is not None and not self.source.abstract(method):
                    return self._traced(
                        function, self.function(method, stack, receiver=definition.name)
                    )
                return (
                    ExceptionFlow(
                        unknowns=[
                            UnknownValue(
                                "unresolved application callback",
                                function.name,
                                argument.lineno,
                            )
                        ]
                    )
                    if method is not None
                    else ExceptionFlow()
                )
            return self._construct(definition, function, stack, line=argument.lineno)
        if definition.owner and self.source.abstract(definition):
            # A bound port method runs one of its implementations.
            implementations = self._implementations(definition)
            if not implementations:
                return ExceptionFlow(
                    unknowns=[
                        UnknownValue(
                            "abstract method without scanned implementation: "
                            f"{definition.name}",
                            function.name,
                            argument.lineno,
                        )
                    ]
                )
            flow = ExceptionFlow()
            for implementation, owner in implementations.items():
                flow.extend(
                    self._traced(
                        function,
                        self.function(
                            implementation,
                            stack,
                            iterated=_generator(implementation.node),
                            receiver=owner,
                        ),
                    )
                )
            return flow
        return self._traced(
            function,
            self.function(
                definition,
                stack,
                iterated=_generator(definition.node),
                receiver=inferred.receiver,
            ),
        )

    def _traced(
        self, function: SourceDefinition, called: ExceptionFlow
    ) -> ExceptionFlow:
        escapes = [
            replace(item, trace=(function.name, *item.trace)) for item in called.escapes
        ]
        return ExceptionFlow(escapes, list(called.unknowns))

    def _dispatch(
        self,
        node: ast.Call,
        port: SourceDefinition,
        function: SourceDefinition,
        *,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        """Follow an abstract call to the implementations that can receive it.

        Dishka providers in the source bind ports to implementations. Without
        a statically known binding, every concrete scanned implementation of
        the port can receive the call; the call contributes their union.
        """
        flow = ExceptionFlow()
        candidates = self._implementations(port)
        provided = (
            self.source.provided(port.owner or "")
            if not candidates and port.node.name == "__call__"
            else None
        )
        followed = (
            self._follow(
                node,
                provided,
                function=function,
                stack=stack,
                awaited=awaited,
                iterated=iterated,
            )
            if provided is not None
            else None
        )
        if followed is not None:
            # A callable port that providers satisfy with functions or lambdas.
            return followed
        if not candidates:
            flow.add_unknown(
                UnknownValue(
                    f"abstract method without scanned implementation: {port.name}",
                    function.name,
                    node.lineno,
                )
            )
        for candidate, implementation in candidates.items():
            flow.extend(
                self._call(
                    node,
                    candidate,
                    function,
                    stack=stack,
                    awaited=awaited,
                    iterated=iterated,
                    receiver=implementation,
                )
            )
        return flow

    def _transport(
        self,
        node: ast.Call,
        callee: SourceDefinition,
        function: SourceDefinition,
        *,
        env: SymbolEnvironment,
    ) -> str | None:
        # An HTTP request passed to a connection parameter keeps the callee
        # on the HTTP transport, so its WebSocket branches are unreachable.
        for argument in [*node.args, *(item.value for item in node.keywords)]:
            value = self.source.infer(argument, function.module, env)
            if self.source.http_request(value) and self.source.http_connection(
                callee, node, argument
            ):
                return "http"
        return None

    def _callables(
        self,
        node: ast.Call,
        callee: SourceDefinition,
        function: SourceDefinition,
        *,
        env: SymbolEnvironment,
    ) -> _Callables:
        # Application functions and bound methods passed as arguments, so a
        # callee that calls its parameter follows the callable it received.
        parameters = self.source.parameters(callee, node)
        result: dict[str, SymbolValue] = {}
        for argument in [*node.args, *(item.value for item in node.keywords)]:
            parameter = parameters.get(id(argument))
            value = self._callable(argument, function.module, env)
            if parameter is not None and value is not None:
                result[parameter.arg] = value
        return tuple(sorted(result.items(), key=lambda item: item[0]))

    def _held(
        self,
        node: ast.Call,
        owner: str,
        *,
        function: SourceDefinition,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow | None:
        # Calling an attribute runs what each construction stored in it.
        assert isinstance(node.func, ast.Attribute)
        sources = self.source.attribute_sources(owner, node.func.attr)
        if sources is None:
            return None
        return self._follow(
            node,
            sources,
            function=function,
            stack=stack,
            awaited=awaited,
            iterated=iterated,
        )

    def _follow(
        self,
        node: ast.Call,
        sources: Iterable[tuple[SourceDefinition | None, str, ast.expr]],
        *,
        function: SourceDefinition,
        stack: tuple[str, ...],
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow | None:
        # Call every callable that the source expressions evaluate to;
        # None when one of them is not statically known. A value that no
        # scanned code supplies comes from elsewhere and stays unknown.
        flow = ExceptionFlow()
        pending = list(sources)
        if not pending:
            return None
        seen: set[int] = set()
        while pending:
            site, module, expression = pending.pop()
            if id(expression) in seen:
                continue
            seen.add(id(expression))
            env = self._site_environment(site)
            if isinstance(expression, ast.Lambda):
                if site is None:
                    return None
                flow.extend(self._callback(expression, site, env, stack=stack))
                continue
            if site is not None and isinstance(expression, ast.Name):
                forwarded = self._forwarded(site, expression.id)
                if forwarded == []:
                    return None
                if forwarded is not None:
                    pending.extend(forwarded)
                    continue
            value = self._callable(expression, module, env)
            definition = self.source.definition(value.name if value else "")
            if value is not None and definition is not None:
                if site is not None and any(
                    statement is definition.node
                    for statement in getattr(site.node, "body", [])
                ):
                    # A nested function sees the parameters of its definer.
                    self._closures.setdefault(definition.name, env)
                flow.extend(
                    self._call(
                        node,
                        definition,
                        function,
                        stack=stack,
                        awaited=awaited,
                        iterated=iterated,
                        receiver=value.receiver,
                    )
                )
            elif self.source.origin(expression, module, env) is None:
                return None
        return flow

    def _forwarded(
        self, site: SourceDefinition, name: str
    ) -> list[tuple[SourceDefinition | None, str, ast.expr]] | None:
        # A parameter that a constructor stores, or that Dishka fills for a
        # provider method: follow what constructions pass or providers return.
        node = site.node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return None
        if self.source.provider_method(site):
            provided = self.source.injected_argument(site, name)
            return list(provided) if provided is not None else None
        return self.source.parameter_sources(site, name)

    def _site_environment(self, site: SourceDefinition | None) -> SymbolEnvironment:
        # Parameters and nested functions visible where a value was passed.
        if site is None or not isinstance(
            site.node, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            return {}
        env = {
            **self._closures.get(site.name, {}),
            **self.source.environment(site),
        }
        for node in site.node.body:
            local = self.source.local_definition(node)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and local:
                env[node.name] = SymbolValue(local.name)
        return env

    def _lambdas(
        self,
        node: ast.Call,
        function: SourceDefinition,
        env: SymbolEnvironment,
        *,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        # A lambda passed to application code may run; its body is analyzed
        # here, and the parameter it binds counts as already followed.
        flow = ExceptionFlow()
        for argument in [*node.args, *(item.value for item in node.keywords)]:
            if isinstance(argument, ast.Lambda):
                flow.extend(self._callback(argument, function, env, stack=stack))
        return flow

    def _callable(
        self, node: ast.expr, module: str, env: SymbolEnvironment
    ) -> SymbolValue | None:
        if isinstance(node, ast.Lambda):
            return SymbolValue(LIBRARY_VALUE, instance=True)
        if (
            isinstance(node, ast.Call)
            and node.args
            and self.source.resolve(node.func, module, env) == "functools.partial"
        ):
            # Calling a partial calls the wrapped callable.
            return self._callable(node.args[0], module, env)
        value = self.source.infer(node, module, env)
        definition = self.source.definition(value.name)
        if (
            value.instance
            or definition is None
            or not isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
        ):
            # A library function or method raises no application errors.
            library = (
                not value.instance
                and definition is None
                and self.source.origin(node, module, env)
            )
            return SymbolValue(LIBRARY_VALUE, instance=True) if library else None
        return value

    def _implementations(self, port: SourceDefinition) -> dict[SourceDefinition, str]:
        # Concrete members that implement *port*, keyed to their class.
        assert port.owner is not None
        assert isinstance(port.node, ast.FunctionDef | ast.AsyncFunctionDef)
        candidates: dict[SourceDefinition, str] = {}
        for implementation in self._implementing_classes(port.owner):
            method = self.source.method(implementation, port.node.name)
            if method is not None and not self.source.abstract(method):
                candidates.setdefault(method, implementation)
        return candidates

    def _implementing_classes(self, owner: str) -> list[str]:
        # Dishka bindings in the source, or every scanned implementation
        # when a binding is missing or not statically known.
        bound = self.source.implementations(owner)
        classes = [item for item in bound if item is not None]
        if len(classes) != len(bound) or not bound:
            classes.extend(self.source.implementers(owner))
        return list(dict.fromkeys(classes))

    def _invalidate(self, node: ast.AST, env: SymbolEnvironment) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Name | ast.Attribute) and isinstance(
                child.ctx, ast.Store
            ):
                env[self.source.key(child)] = SymbolValue()


HTTP_ROUTE_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route"}
)
TRANSPORT_METHODS = {"session": "webrtc"}
"""Decorator names of non-HTTP route transports, mapped to their transport."""
RPC_METHOD = "method"


def _operation_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Attribute):
        return decorator.attr == RPC_METHOD
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {*HTTP_ROUTE_METHODS, *TRANSPORT_METHODS, RPC_METHOD}
    )


def _super(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "super"
    )


def _ambiguous(subject: str, candidates: Iterable[str]) -> str:
    names = list(dict.fromkeys(candidates))
    shown = ", ".join(names[:_SHOWN_CANDIDATES])
    if len(names) > _SHOWN_CANDIDATES:
        shown += f" and {len(names) - _SHOWN_CANDIDATES} more"
    return f"ambiguous {subject}: candidates {shown}; annotate the receiver"


def _signature(escape: _Escape) -> tuple[str, bool]:
    return escape.exception, escape.deferred


def _pattern_names(pattern: ast.pattern) -> list[str]:
    names: list[str] = []
    for child in ast.walk(pattern):
        match child:
            case ast.MatchAs(name=str(name)) | ast.MatchStar(name=str(name)):
                names.append(name)
            case ast.MatchMapping(rest=str(name)):
                names.append(name)
    return names


def _always_raises(node: ast.AST) -> bool:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        for statement in node.body:
            if isinstance(statement, ast.Return):
                return False
            if isinstance(statement, ast.Raise):
                return True
            if not isinstance(
                statement, ast.Expr | ast.Assign | ast.AnnAssign | ast.Pass
            ):
                return False
    return False


def _suppression(node: ast.AST) -> bool | None:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    returns = [
        child for child in _executed_nodes(node.body) if isinstance(child, ast.Return)
    ]
    if not returns:
        return False
    # The bounded protocol model does not evaluate conditional return values.
    if len(returns) != 1 or returns[0] not in node.body:
        return None
    value = returns[0].value
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        if value.value is None or value.value is False:
            return False
        if value.value is True:
            return True
    return None


def _generator(node: ast.AST) -> bool:
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
        isinstance(child, ast.Yield | ast.YieldFrom)
        for child in _executed_nodes(node.body)
    )


def _accepts(definition: SourceDefinition, node: ast.Call, *, bound: bool) -> bool:
    """Whether the arguments of *node* bind to the parameters of *definition*."""
    callee = definition.node
    if not isinstance(callee, ast.FunctionDef | ast.AsyncFunctionDef):
        return True
    if any(isinstance(item, ast.Starred) for item in node.args) or any(
        item.arg is None for item in node.keywords
    ):
        return True
    arguments = callee.args
    positional = [*arguments.posonlyargs, *arguments.args]
    only = len(arguments.posonlyargs)
    if bound and positional:
        positional, only = positional[1:], max(only - 1, 0)
    if len(node.args) > len(positional) and arguments.vararg is None:
        return False
    filled = {item.arg for item in positional[: len(node.args)]}
    named = {item.arg for item in [*positional[only:], *arguments.kwonlyargs]}
    for keyword in node.keywords:
        assert keyword.arg is not None
        if keyword.arg in filled or (
            keyword.arg not in named and arguments.kwarg is None
        ):
            return False
        filled.add(keyword.arg)
    defaults = len(arguments.defaults)
    required = [
        *(item.arg for item in positional[: len(positional) - defaults]),
        *(
            item.arg
            for item, default in zip(
                arguments.kwonlyargs, arguments.kw_defaults, strict=True
            )
            if default is None
        ),
    ]
    return all(name in filled for name in required)


def _exits(body: list[ast.stmt]) -> bool:
    """Whether *body* always leaves its block before the next statement."""
    return any(
        isinstance(statement, ast.Return | ast.Raise | ast.Continue | ast.Break)
        for statement in body
    )


def _executed_nodes(body: list[ast.stmt]) -> list[ast.AST]:
    result: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            return
        result.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for node in body:
        visit(node)
    return result


class _Scope(NamedTuple):
    stack: tuple[str, ...]
    reraised: tuple[_Escape, ...] | None
    reraised_name: str | None


type _Callables = tuple[tuple[str, SymbolValue], ...]


class _SummaryKey(NamedTuple):
    function: str
    iterated: bool
    receiver: str | None
    dependency_transport: str | None
    callables: _Callables


class _Summary(NamedTuple):
    escapes: tuple[_Escape, ...]
    unknowns: tuple[UnknownValue, ...]
    modules: frozenset[str]
