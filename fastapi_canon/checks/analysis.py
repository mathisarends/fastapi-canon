"""Explicit-exception propagation for the supported, statically resolved subset."""

import ast
from dataclasses import dataclass, field

from fastapi_canon.checks.source import (
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


@dataclass(frozen=True)
class _Escape:
    exception: str
    trace: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class UnknownValue:
    message: str
    function: str
    line: int


@dataclass
class ExceptionFlow:
    escapes: list[_Escape] = field(default_factory=list)
    unknowns: list[UnknownValue] = field(default_factory=list)

    def extend(self, other: "ExceptionFlow") -> None:
        self.escapes.extend(other.escapes)
        self.unknowns.extend(other.unknowns)


class ContractAnalyzer:
    def __init__(self, source: SourceIndex) -> None:
        self.source = source
        self._summaries: dict[
            tuple[str, bool, str | None, str | None],
            tuple[tuple[_Escape, ...], tuple[UnknownValue, ...], frozenset[str]],
        ] = {}

    def function(
        self,
        function: SourceDefinition,
        stack: tuple[str, ...] = (),
        *,
        iterated: bool = False,
        receiver: str | None = None,
        dependency_transport: str | None = None,
    ) -> ExceptionFlow:
        """Summarize *function*; iterated generators execute their bodies."""
        if function.name in stack:
            return ExceptionFlow(
                unknowns=[
                    UnknownValue("recursive call", function.name, function.node.lineno)
                ]
            )
        summary_key = function.name, iterated, receiver, dependency_transport
        if summary_key in self._summaries:
            escapes, unknowns, modules = self._summaries[summary_key]
            self.source.touched.update(modules)
            return ExceptionFlow(list(escapes), list(unknowns))
        previous = self.source.touched
        self.source.touched = set()
        try:
            flow = self._analyze_function(
                function,
                stack,
                iterated=iterated,
                receiver=receiver,
                dependency_transport=dependency_transport,
            )
            # The report retains the first path per exception. Propagation and
            # catching depend on exception types, not duplicate paths to them.
            first: dict[str, _Escape] = {}
            for item in flow.escapes:
                first.setdefault(item.exception, item)
            flow.escapes = list(first.values())
            flow.unknowns = list(dict.fromkeys(flow.unknowns))
            if not any(item.message == "recursive call" for item in flow.unknowns):
                self._summaries[summary_key] = (
                    tuple(flow.escapes),
                    tuple(flow.unknowns),
                    frozenset(self.source.touched),
                )
            return flow
        finally:
            previous.update(self.source.touched)
            self.source.touched = previous

    def _analyze_function(
        self,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        iterated: bool,
        receiver: str | None = None,
        dependency_transport: str | None = None,
    ) -> ExceptionFlow:
        node = function.node
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        flow = ExceptionFlow()
        for decorator in node.decorator_list:
            name = self.source.resolve(decorator, function.module)
            if name == "abc.abstractmethod":
                return ExceptionFlow(
                    unknowns=[
                        UnknownValue(
                            "abstract implementation", function.name, node.lineno
                        )
                    ]
                )
            if name not in {
                "dishka.integrations.fastapi.inject",
                "builtins.staticmethod",
                "builtins.classmethod",
                "builtins.property",
            } and not _operation_decorator(decorator):
                flow.unknowns.append(
                    UnknownValue(
                        "unresolved decorator", function.name, decorator.lineno
                    )
                )
        if not iterated and _generator(node):
            flow.unknowns.append(
                UnknownValue(
                    "generator execution is outside the HTTP response boundary",
                    function.name,
                    node.lineno,
                )
            )
            return flow
        env = self.source.environment(function, receiver, dependency_transport)
        flow.extend(self.block(node.body, function, env, (*stack, function.name)))
        return flow

    def expression(
        self,
        node: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        stack: tuple[str, ...],
        *,
        awaited: bool = False,
        iterated: bool = False,
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        if isinstance(node, ast.Await):
            return self.expression(
                node.value, function, env, stack, awaited=True, iterated=iterated
            )
        if isinstance(node, ast.Lambda):
            return flow
        if isinstance(node, ast.GeneratorExp) and not iterated:
            # Creation evaluates the outer iterable expression, but neither the
            # filters nor the result until a consumer advances the generator.
            flow.extend(self.expression(node.generators[0].iter, function, env, stack))
            flow.unknowns.append(
                UnknownValue(
                    "generator expression execution is deferred until iterated",
                    function.name,
                    node.lineno,
                )
            )
            return flow
        if isinstance(
            node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
        ):
            return self._comprehension(node, function, env, stack)
        if isinstance(node, ast.NamedExpr):
            flow.unknowns.append(
                UnknownValue(
                    f"unsupported expression: {type(node).__name__}",
                    function.name,
                    node.lineno,
                )
            )
        if isinstance(node, ast.Call):
            for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                flow.extend(self.expression(argument, function, env, stack))
            target = self.source.infer(node.func, function.module, env)
            name = target.name or self.source.resolve(node.func, function.module, env)
            definition = self.source.definitions.get(name or "")
            if definition and isinstance(node.func, ast.Attribute | ast.Call):
                receiver_expression = (
                    node.func.value
                    if isinstance(node.func, ast.Attribute)
                    else node.func
                )
                flow.extend(self.expression(receiver_expression, function, env, stack))
            if name and name.startswith("builtins.") and self.source.exception(name):
                return flow
            if definition and isinstance(definition.node, ast.ClassDef):
                if target.instance:
                    method = self.source.method(definition.name, "__call__")
                    if method is None:
                        flow.unknowns.append(
                            UnknownValue(
                                f"unresolved callable instance: {ast.unparse(node.func)}",
                                function.name,
                                node.lineno,
                            )
                        )
                    elif self.source.abstract(method):
                        flow.extend(
                            self._dispatch(
                                node,
                                method,
                                function,
                                stack,
                                awaited=awaited,
                                iterated=iterated,
                            )
                        )
                    else:
                        flow.extend(
                            self._call(
                                node,
                                method,
                                function,
                                stack,
                                awaited=awaited,
                                iterated=iterated,
                                receiver=definition.name,
                            )
                        )
                else:
                    flow.extend(
                        self._construct(definition, function, stack, node.lineno)
                    )
            elif definition and definition.owner and self.source.abstract(definition):
                flow.extend(
                    self._dispatch(
                        node,
                        definition,
                        function,
                        stack,
                        awaited=awaited,
                        iterated=iterated,
                    )
                )
            elif definition:
                if definition.owner and isinstance(node.func, ast.Attribute):
                    receiver = self.source.resolve(
                        node.func.value, function.module, env
                    )
                    if (
                        receiver
                        and receiver != definition.owner
                        and not self.source.classmethod(definition)
                    ):
                        flow.unknowns.append(
                            UnknownValue(
                                "inherited method dispatch may use subclass overrides",
                                function.name,
                                node.lineno,
                            )
                        )
                flow.extend(
                    self._call(
                        node,
                        definition,
                        function,
                        stack,
                        awaited=awaited,
                        iterated=iterated,
                        receiver=target.receiver,
                    )
                )
            elif self.source.origin(node.func, function.module, env) is not None:
                # Library code raises no application exceptions of its own; it
                # reaches them only through receivers and callables it is given.
                if isinstance(node.func, ast.Attribute | ast.Call):
                    evaluated: ast.expr = (
                        node.func.value
                        if isinstance(node.func, ast.Attribute)
                        else node.func
                    )
                    flow.extend(self.expression(evaluated, function, env, stack))
                for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                    flow.extend(self._callback(argument, function, env, stack))
            else:
                flow.unknowns.append(
                    UnknownValue(
                        f"unresolved call: {ast.unparse(node.func)}",
                        function.name,
                        node.lineno,
                    )
                )
            return flow
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                flow.extend(self.expression(child, function, env, stack))
        if isinstance(node, ast.Attribute):
            name = self.source.resolve(node, function.module, env)
            member = self.source.definitions.get(name or "")
            if (
                member
                and isinstance(member.node, ast.FunctionDef)
                and any(
                    self.source.resolve(decorator, member.module) == "builtins.property"
                    for decorator in member.node.decorator_list
                )
            ):
                flow.extend(self.function(member, stack))
        return flow

    def _comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        function: SourceDefinition,
        env: SymbolEnvironment,
        stack: tuple[str, ...],
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        local = env.copy()
        # All targets are lexical locals, even in filters before a later for.
        # The first iterable alone is evaluated in the enclosing environment.
        for generator in node.generators:
            self._invalidate(generator.target, local)
            if any(
                isinstance(child, ast.Attribute | ast.Subscript)
                for child in ast.walk(generator.target)
            ):
                flow.unknowns.append(
                    UnknownValue(
                        "unsupported comprehension target",
                        function.name,
                        generator.target.lineno,
                    )
                )
        for index, generator in enumerate(node.generators):
            flow.extend(
                self._comprehension_iterable(
                    generator.iter,
                    function,
                    env if index == 0 else local,
                    stack,
                    asynchronous=bool(generator.is_async),
                )
            )
            for condition in generator.ifs:
                flow.extend(self.expression(condition, function, local, stack))
        values = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        for value in values:
            flow.extend(self.expression(value, function, local, stack))
        return flow

    def _comprehension_iterable(
        self,
        node: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
        stack: tuple[str, ...],
        *,
        asynchronous: bool,
    ) -> ExceptionFlow:
        flow = self.expression(node, function, env, stack, iterated=True)
        target = node.func if isinstance(node, ast.Call) else node
        definition = self.source.definitions.get(
            self.source.resolve(target, function.module, env) or ""
        )
        value_type = definition
        if (
            isinstance(node, ast.Call)
            and definition
            and isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
            and definition.node.returns
        ):
            value_type = self.source.definitions.get(
                self.source.annotation(definition.node.returns, definition.module) or ""
            )
        if value_type and isinstance(value_type.node, ast.ClassDef):
            flow.unknowns.append(
                UnknownValue(
                    "unsupported comprehension iteration protocol",
                    function.name,
                    node.lineno,
                )
            )
        if asynchronous and not (
            isinstance(node, ast.GeneratorExp)
            or (
                definition
                and isinstance(definition.node, ast.AsyncFunctionDef)
                and _generator(definition.node)
            )
        ):
            flow.unknowns.append(
                UnknownValue(
                    "unresolved async comprehension iterator",
                    function.name,
                    node.lineno,
                )
            )
        return flow

    def _context_manager(
        self,
        node: ast.With | ast.AsyncWith,
        function: SourceDefinition,
        env: SymbolEnvironment,
        stack: tuple[str, ...],
        reraised: tuple[_Escape, ...],
        index: int = 0,
    ) -> ExceptionFlow:
        if index == len(node.items):
            return self.block(node.body, function, env, stack, reraised)
        item = node.items[index]
        flow = self.expression(item.context_expr, function, env, stack)
        if (
            isinstance(node, ast.With)
            and isinstance(item.context_expr, ast.Call)
            and self.source.resolve(item.context_expr.func, function.module, env)
            == "contextlib.suppress"
        ):
            local = env.copy()
            if item.optional_vars is not None:
                self._invalidate(item.optional_vars, local)
            inner = self._context_manager(
                node, function, local, stack, reraised, index + 1
            )
            names = [
                self.source.resolve(argument, function.module, env)
                for argument in item.context_expr.args
            ]
            if item.context_expr.keywords or any(
                name is None
                or not self.source.exception(name)
                or self.source.mro(name) is None
                for name in names
            ):
                flow.unknowns.append(
                    UnknownValue(
                        "unresolved contextlib.suppress exception types",
                        function.name,
                        item.context_expr.lineno,
                    )
                )
            else:
                inner.escapes = [
                    escape
                    for escape in inner.escapes
                    if not any(
                        name in (self.source.mro(escape.exception) or [])
                        for name in names
                    )
                ]
            flow.extend(inner)
            return flow
        manager = self._context_type(item.context_expr, function, env)
        asynchronous = isinstance(node, ast.AsyncWith)
        enter = (
            self.source.method(manager, "__aenter__" if asynchronous else "__enter__")
            if manager
            else None
        )
        exit_method = (
            self.source.method(manager, "__aexit__" if asynchronous else "__exit__")
            if manager
            else None
        )
        supported = enter is not None and exit_method is not None
        for method in (enter, exit_method):
            if method is None:
                continue
            method_node = method.node
            if (
                not isinstance(method_node, ast.FunctionDef | ast.AsyncFunctionDef)
                or isinstance(method_node, ast.AsyncFunctionDef) != asynchronous
                or _generator(method_node)
                or method_node.decorator_list
                or method.owner != manager
            ):
                supported = False
        if not supported:
            flow.unknowns.append(
                UnknownValue(
                    "unresolved or unsupported context manager protocol",
                    function.name,
                    item.context_expr.lineno,
                )
            )
        else:
            assert enter is not None
            flow.extend(self._traced(function, self.function(enter, stack)))
            if _always_raises(enter.node):
                return flow
        local = env.copy()
        if item.optional_vars is not None:
            self._invalidate(item.optional_vars, local)
            if isinstance(item.optional_vars, ast.Name) and supported and enter:
                enter_node = enter.node
                assert isinstance(enter_node, ast.FunctionDef | ast.AsyncFunctionDef)
                returned = (
                    self.source.annotation(enter_node.returns, enter.module)
                    if enter_node.returns
                    else None
                )
                if returned in {"typing.Self", "typing_extensions.Self"}:
                    returned = manager
                local[item.optional_vars.id] = SymbolValue(
                    returned or "", instance=True
                )
            elif not isinstance(item.optional_vars, ast.Name):
                flow.unknowns.append(
                    UnknownValue(
                        "unsupported context manager binding target",
                        function.name,
                        item.optional_vars.lineno,
                    )
                )
        inner = self._context_manager(node, function, local, stack, reraised, index + 1)
        if supported:
            assert exit_method is not None
            mode = _suppression(exit_method.node)
            if mode is None:
                flow.unknowns.append(
                    UnknownValue(
                        "context manager exception suppression is unresolved",
                        function.name,
                        item.context_expr.lineno,
                    )
                )
            if mode is True or _always_raises(exit_method.node):
                inner.escapes.clear()
            flow.extend(inner)
            flow.extend(self._traced(function, self.function(exit_method, stack)))
        else:
            flow.extend(inner)
        return flow

    def _context_type(
        self, node: ast.expr, function: SourceDefinition, env: SymbolEnvironment
    ) -> str | None:
        awaited = isinstance(node, ast.Await)
        expression = node.value if isinstance(node, ast.Await) else node
        target = expression.func if isinstance(expression, ast.Call) else expression
        resolved = self.source.resolve(target, function.module, env)
        definition = self.source.definitions.get(resolved or "")
        if isinstance(expression, ast.Call) and definition:
            if isinstance(definition.node, ast.ClassDef):
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
                result = self.source.definitions.get(returned or "")
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
        line: int,
    ) -> ExceptionFlow:
        """Follow the application code that constructing *definition* runs."""
        flow = ExceptionFlow()
        lineage = self.source.lineage(definition.name)
        if lineage is None:
            flow.unknowns.append(
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
                flow.unknowns.append(
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
            flow.unknowns.append(
                UnknownValue(
                    "class decorators or metaclasses may change construction",
                    function.name,
                    line,
                )
            )
        hooks = ["__new__", "__init__"]
        if external:
            # Library-generated constructors call these application hooks.
            hooks.extend(["__post_init__", "__attrs_post_init__", "model_post_init"])
        for hook in hooks:
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
            for owner in classes:
                owner_node = self.source.definitions[owner].node
                assert isinstance(owner_node, ast.ClassDef)
                for member_node in owner_node.body:
                    if isinstance(
                        member_node, ast.FunctionDef | ast.AsyncFunctionDef
                    ) and any(
                        self.source.resolve(
                            item.func if isinstance(item, ast.Call) else item,
                            self.source.definitions[owner].module,
                        )
                        not in _PLAIN_DECORATORS
                        for item in member_node.decorator_list
                    ):
                        flow.unknowns.append(
                            UnknownValue(
                                "library construction may call decorated hook: "
                                f"{owner}.{member_node.name}",
                                function.name,
                                line,
                            )
                        )
        return flow

    def _callback(
        self,
        argument: ast.expr,
        function: SourceDefinition,
        env: SymbolEnvironment,
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
            return self.expression(argument.body, function, lambda_env, stack)
        if not isinstance(argument, ast.Name | ast.Attribute | ast.Call):
            return ExceptionFlow()
        inferred = self.source.infer(argument, function.module, env)
        name = inferred.name or self.source.resolve(argument, function.module, env)
        definition = self.source.definitions.get(name or "")
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
            return self._construct(definition, function, stack, argument.lineno)
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
        return ExceptionFlow(
            [
                _Escape(item.exception, (function.name, *item.trace), item.line)
                for item in called.escapes
            ],
            list(called.unknowns),
        )

    def _call(
        self,
        node: ast.Call,
        definition: SourceDefinition,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        awaited: bool,
        iterated: bool,
        receiver: str | None = None,
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        iterated_generator = iterated and _generator(definition.node)
        if (
            isinstance(definition.node, ast.AsyncFunctionDef)
            and not awaited
            and not iterated_generator
        ):
            flow.unknowns.append(
                UnknownValue(
                    "coroutine execution is deferred until awaited",
                    function.name,
                    node.lineno,
                )
            )
            return flow
        called = self.function(
            definition, stack, iterated=iterated_generator, receiver=receiver
        )
        flow.escapes.extend(
            _Escape(item.exception, (function.name, *item.trace), item.line)
            for item in called.escapes
        )
        flow.unknowns.extend(called.unknowns)
        return flow

    def _dispatch(
        self,
        node: ast.Call,
        port: SourceDefinition,
        function: SourceDefinition,
        stack: tuple[str, ...],
        *,
        awaited: bool,
        iterated: bool,
    ) -> ExceptionFlow:
        """Follow an abstract call to the implementations that can receive it.

        Dishka providers in the source bind ports to implementations. Abstract
        calls on ``self`` without a binding dispatch to concrete subclasses.
        Multiple candidates contribute the union of their escapes.
        """
        assert port.owner is not None
        assert isinstance(port.node, ast.FunctionDef | ast.AsyncFunctionDef)
        flow = ExceptionFlow()
        bound = self.source.implementations(port.owner)
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        candidates: list[SourceDefinition] = []
        if bound:
            for implementation in bound:
                method = (
                    self.source.method(implementation, port.node.name)
                    if implementation
                    else None
                )
                if method is None or self.source.abstract(method):
                    flow.unknowns.append(
                        UnknownValue(
                            f"unresolved Dishka implementation of {port.name}",
                            function.name,
                            node.lineno,
                        )
                    )
                else:
                    candidates.append(method)
        elif isinstance(receiver, ast.Name) and receiver.id == "self":
            for subclass in self.source.subclasses(port.owner):
                method = self.source.method(subclass, port.node.name)
                if method is not None and not self.source.abstract(method):
                    candidates.append(method)
        if not bound and not candidates:
            flow.unknowns.append(
                UnknownValue(
                    f"abstract implementation without Dishka binding: {port.name}",
                    function.name,
                    node.lineno,
                )
            )
        unique = {candidate.name: candidate for candidate in candidates}
        for candidate in unique.values():
            flow.extend(
                self._call(
                    node,
                    candidate,
                    function,
                    stack,
                    awaited=awaited,
                    iterated=iterated,
                )
            )
        return flow

    def block(
        self,
        body: list[ast.stmt],
        function: SourceDefinition,
        env: SymbolEnvironment,
        stack: tuple[str, ...],
        reraised: tuple[_Escape, ...] = (),
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                # Nested definitions are not executed, but their bodies cannot be resolved.
                env[node.name] = SymbolValue()
                for expression in node.decorator_list:
                    flow.extend(self.expression(expression, function, env, stack))
                if isinstance(node, ast.ClassDef):
                    flow.unknowns.append(
                        UnknownValue(
                            "nested class construction", function.name, node.lineno
                        )
                    )
                else:
                    for expression in [
                        *node.args.defaults,
                        *(value for value in node.args.kw_defaults if value),
                    ]:
                        flow.extend(self.expression(expression, function, env, stack))
                continue
            if isinstance(node, ast.Raise):
                if node.exc is None:
                    if reraised:
                        flow.escapes.extend(reraised)
                    else:
                        flow.unknowns.append(
                            UnknownValue(
                                "unresolved re-raise", function.name, node.lineno
                            )
                        )
                else:
                    flow.extend(self.expression(node.exc, function, env, stack))
                    if node.cause:
                        flow.extend(self.expression(node.cause, function, env, stack))
                    target = (
                        node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                    )
                    name = self.source.resolve(target, function.module, env)
                    if name and self.source.exception(name):
                        flow.escapes.append(
                            _Escape(name, (function.name,), node.lineno)
                        )
                    else:
                        flow.unknowns.append(
                            UnknownValue(
                                f"unresolved raised exception: {ast.unparse(node.exc)}",
                                function.name,
                                node.lineno,
                            )
                        )
                break
            if isinstance(node, ast.Try):
                attempted = self.block(node.body, function, env.copy(), stack, reraised)
                flow.unknowns.extend(attempted.unknowns)
                remaining = attempted.escapes
                for handler in node.handlers:
                    types = (
                        handler.type.elts
                        if isinstance(handler.type, ast.Tuple)
                        else [handler.type]
                    )
                    names = [
                        self.source.resolve(item, function.module, env)
                        for item in types
                        if item is not None
                    ]
                    unknown_catch = handler.type is not None and (
                        not names
                        or any(
                            name is None or self.source.mro(name) is None
                            for name in names
                        )
                    )
                    if unknown_catch:
                        flow.unknowns.append(
                            UnknownValue(
                                "unresolved exception handler",
                                function.name,
                                handler.lineno,
                            )
                        )
                    caught = [
                        item
                        for item in remaining
                        if handler.type is None
                        or any(
                            name in (self.source.mro(item.exception) or [])
                            for name in names
                            if name
                        )
                    ]
                    remaining = [item for item in remaining if item not in caught]
                    handler_env = env.copy()
                    caught_types = {item.exception for item in caught}
                    if handler.name:
                        handler_env[handler.name] = SymbolValue(
                            next(iter(caught_types)) if len(caught_types) == 1 else "",
                            instance=True,
                        )
                    # Implicit failures (e.g. int(value)) can reach handlers too.
                    flow.extend(
                        self.block(
                            handler.body, function, handler_env, stack, tuple(caught)
                        )
                    )
                flow.escapes.extend(remaining)
                flow.extend(
                    self.block(node.orelse, function, env.copy(), stack, reraised)
                )
                flow.extend(
                    self.block(node.finalbody, function, env.copy(), stack, reraised)
                )
                if any(
                    isinstance(child, ast.Return | ast.Raise)
                    for child in _executed_nodes(node.finalbody)
                ):
                    flow.unknowns.append(
                        UnknownValue(
                            "finally may replace an escaping exception",
                            function.name,
                            node.lineno,
                        )
                    )
                self._invalidate(node, env)
                continue
            if isinstance(node, ast.With | ast.AsyncWith):
                flow.extend(self._context_manager(node, function, env, stack, reraised))
                self._invalidate(node, env)
                continue
            if isinstance(node, ast.If):
                flow.extend(self.expression(node.test, function, env, stack))
                condition = self.source.condition(node.test, function.module, env)
                if condition is not False:
                    flow.extend(
                        self.block(node.body, function, env.copy(), stack, reraised)
                    )
                if condition is not True:
                    flow.extend(
                        self.block(node.orelse, function, env.copy(), stack, reraised)
                    )
                self._invalidate(node, env)
                selected = node.body if condition else node.orelse
                if condition is not None and any(
                    isinstance(statement, ast.Return | ast.Raise)
                    for statement in selected
                ):
                    break
                continue
            if isinstance(node, ast.For | ast.AsyncFor | ast.While):
                # Iterations may run zero or more times, like independent branches.
                if isinstance(node, ast.While):
                    flow.extend(self.expression(node.test, function, env, stack))
                else:
                    flow.extend(
                        self.expression(node.iter, function, env, stack, iterated=True)
                    )
                    self._invalidate(node.target, env)
                flow.extend(
                    self.block(node.body, function, env.copy(), stack, reraised)
                )
                flow.extend(
                    self.block(node.orelse, function, env.copy(), stack, reraised)
                )
                self._invalidate(node, env)
                continue
            if isinstance(node, ast.Assign | ast.AnnAssign):
                if node.value is not None:
                    flow.extend(self.expression(node.value, function, env, stack))
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                inferred = SymbolValue()
                if isinstance(node, ast.AnnAssign):
                    inferred = SymbolValue(
                        self.source.annotation(node.annotation, function.module) or "",
                        instance=True,
                    )
                if not inferred.name and node.value is not None:
                    inferred = self.source.infer(node.value, function.module, env)
                for target in targets:
                    key = self.source.key(target)
                    env[key] = inferred
                continue
            if isinstance(node, ast.Return):
                if node.value:
                    flow.extend(self.expression(node.value, function, env, stack))
                break
            if isinstance(node, ast.Expr):
                flow.extend(self.expression(node.value, function, env, stack))
                continue
            if isinstance(node, ast.Pass | ast.Break | ast.Continue):
                continue
            # Match, try*, etc. need more control-flow knowledge.
            flow.unknowns.append(
                UnknownValue(
                    f"unsupported statement: {type(node).__name__}",
                    function.name,
                    node.lineno,
                )
            )
            for child in _executed_nodes([node]):
                if isinstance(child, ast.Raise) and child.exc:
                    target = (
                        child.exc.func if isinstance(child.exc, ast.Call) else child.exc
                    )
                    name = self.source.resolve(target, function.module, env)
                    if name and self.source.exception(name):
                        flow.escapes.append(
                            _Escape(name, (function.name,), child.lineno)
                        )
            self._invalidate(node, env)
        return flow

    def _invalidate(self, node: ast.AST, env: SymbolEnvironment) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Name | ast.Attribute) and isinstance(
                child.ctx, ast.Store
            ):
                env[self.source.key(child)] = SymbolValue()


HTTP_ROUTE_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route"}
)
TRANSPORT_METHODS = frozenset({"sse", "webrtc"})
RPC_METHOD = "method"


def _operation_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Attribute):
        return decorator.attr == RPC_METHOD
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {*HTTP_ROUTE_METHODS, *TRANSPORT_METHODS, RPC_METHOD}
    )


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
