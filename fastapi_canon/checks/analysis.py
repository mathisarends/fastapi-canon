"""Explicit-exception propagation for the supported, statically resolved subset."""

import ast
from dataclasses import replace

from fastapi_canon.checks.effects import _Escape, _Flow, _Summary, _SummaryKey, _Unknown
from fastapi_canon.checks.generator_phases import _has_yield, _phases
from fastapi_canon.checks.source import _Environment, _Source
from fastapi_canon.checks.symbols import _Definition
from fastapi_canon.checks.value_flow import _contexts, _join, _Value

_PLAIN_DECORATORS = frozenset(
    {
        "abc.abstractmethod",
        "builtins.classmethod",
        "builtins.property",
        "builtins.staticmethod",
        "functools.cached_property",
    }
)
_STACK_EFFECTS = frozenset(
    f"contextlib.{owner}.{method}"
    for owner in ("ExitStack", "AsyncExitStack")
    for method in (
        "close",
        "aclose",
        "__exit__",
        "__aexit__",
        "pop_all",
        "enter_context",
        "enter_async_context",
        "callback",
        "push",
        "push_async_callback",
        "push_async_exit",
    )
)
_VALIDATORS = frozenset(
    {
        "pydantic.field_validator",
        "pydantic.model_validator",
        "pydantic.functional_validators.field_validator",
        "pydantic.functional_validators.model_validator",
        "pydantic.validator",
        "pydantic.root_validator",
    }
)
_SERIALIZERS = frozenset(
    {
        "pydantic.field_serializer",
        "pydantic.model_serializer",
        "pydantic.functional_serializers.field_serializer",
        "pydantic.functional_serializers.model_serializer",
    }
)
_PLAIN_CONTEXTS = frozenset(
    {
        "sqlite3.Connection",
        "io.TextIOWrapper",
        "io.BufferedReader",
        "io.BufferedWriter",
        "io.BufferedRandom",
        "asyncio.Lock",
        "asyncio.locks.Lock",
        "asyncio.Timeout",
        "asyncio.timeouts.Timeout",
        "filelock.FileLock",
    }
)


class _Analyzer:
    def __init__(self, source: _Source) -> None:
        self.source = source
        self._yields: dict[str, _Flow] = {}
        self._continuations: dict[str, tuple[_Definition, list[ast.stmt]]] = {}
        self._summaries: dict[_SummaryKey, _Summary] = {}
        self._active: dict[_SummaryKey, _Flow] = {}
        self._cycles: dict[str, set[str]] = {}
        self._entering: dict[str, frozenset[tuple[int, int]]] = {}
        self._closing: set[str] = set()

    def function(
        self,
        function: _Definition,
        stack: tuple[str, ...] = (),
        *,
        iterated: bool = False,
        receiver: str | None = None,
        dependency_transport: str | None = None,
        bindings: _Environment | None = None,
    ) -> _Flow:
        """Compute a context-sensitive exception summary, including recursive SCCs."""
        if bindings is not None:
            if any(
                self.source.expand(value).name == "<empty>"
                for value in bindings.values()
            ):
                return _Flow()
            variants = _contexts(
                {name: self.source.expand(value) for name, value in bindings.items()}
            )
            if variants is None:
                return _Flow(
                    unknowns=[
                        _Unknown(
                            "argument alternatives exceed the context bound",
                            function.name,
                            function.node.lineno,
                        )
                    ]
                )
            if len(variants) > 1:
                flow = _Flow()
                for variant in variants:
                    flow.extend(
                        self.function(
                            function,
                            stack,
                            iterated=iterated,
                            receiver=receiver,
                            dependency_transport=dependency_transport,
                            bindings=variant,
                        )
                    )
                return flow
            bindings = variants[0]
        key: _SummaryKey = (
            function.name,
            iterated,
            receiver,
            dependency_transport,
            tuple(sorted(bindings.items())) if bindings is not None else None,
        )
        if key in self._active:
            members = set(stack[stack.index(function.name) :]) | {function.name}
            for member in tuple(members):
                members.update(self._cycles.get(member, ()))
            for member in members:
                self._cycles[member] = members
            return self._active[key].copy()
        if (
            len(stack) >= 128
            or sum(item[0] == function.name for item in self._active) >= 32
        ):
            return _Flow(
                unknowns=[
                    _Unknown(
                        "call context did not converge within the analysis bound",
                        function.name,
                        function.node.lineno,
                    )
                ]
            )
        cached = self._summaries.get(key)
        if cached and all(
            self.source.heap.get(site) == value for site, value in cached.reads
        ):
            self.source.touched.update(cached.modules)
            for site, _ in cached.reads:
                self.source.observe(site)
            return cached.flow.copy()
        previous_modules, previous_reads = self.source.touched, self.source.reads
        previous_versions, previous_unstable = (
            self.source.read_versions,
            self.source.unstable_reads,
        )
        self.source.touched, self.source.reads = set(), set()
        self.source.read_versions, self.source.unstable_reads = {}, set()
        self._active[key] = _Flow()
        try:
            for _ in range(32):
                flow = self._analyze_function(
                    function,
                    stack,
                    iterated=iterated,
                    receiver=receiver,
                    dependency_transport=dependency_transport,
                    bindings=bindings,
                )
                group = self._cycles.get(function.name, set())
                first: dict[str, _Escape] = {}
                for escape in flow.escapes:
                    old = first.get(escape.exception)
                    if (
                        old is None
                        or (old.implicit and not escape.implicit)
                        or (
                            group
                            and old.implicit == escape.implicit
                            and len(escape.trace) < len(old.trace)
                        )
                    ):
                        first[escape.exception] = escape
                flow.escapes = list(first.values())
                flow.unknowns = list(dict.fromkeys(flow.unknowns))
                stable = flow.signature == self._active[key].signature
                self._active[key] = flow.copy()
                nested_component = group and any(
                    other != key and other[0] in group for other in self._active
                )
                if not group or stable or nested_component:
                    # Iterate a recursive component at its outermost frame.
                    # Nested independent fixed points multiply work with every
                    # recursive value shape; their caller owns the next round.
                    break
                # A callee summarized against an earlier approximation must be
                # recomputed before the recursive component can be finalized.
                self._summaries = {
                    k: v for k, v in self._summaries.items() if k[0] not in group
                }
            else:
                flow.unknowns.append(
                    _Unknown(
                        "recursive effects did not converge",
                        function.name,
                        function.node.lineno,
                    )
                )
            consistent = not self.source.unstable_reads and all(
                self.source.heap.get(site) == version
                for site, version in self.source.read_versions.items()
            )
            if consistent and not any(
                item.message
                == "call context did not converge within the analysis bound"
                for item in flow.unknowns
            ):
                self._summaries[key] = _Summary(
                    flow.copy(),
                    frozenset(self.source.touched),
                    tuple(
                        sorted(
                            (site, self.source.heap[site])
                            for site in self.source.reads
                            if site in self.source.heap
                        )
                    ),
                )
            group = self._cycles.get(function.name, set())
            if group and not consistent:
                self._summaries = {
                    k: v for k, v in self._summaries.items() if k[0] not in group
                }
            if (
                group
                and consistent
                and not any(k != key and k[0] in group for k in self._active)
            ):
                # Every summary in a recursive component depends on all of the
                # component's heap reads, including reads through provisional calls.
                modules = frozenset(self.source.touched)
                sites = set(self.source.reads)
                for k, summary in self._summaries.items():
                    if k[0] in group:
                        modules |= summary.modules
                        sites.update(site for site, _ in summary.reads)
                reads = tuple(
                    sorted(
                        (site, self.source.heap[site])
                        for site in sites
                        if site in self.source.heap
                    )
                )
                for k, summary in list(self._summaries.items()):
                    if k[0] in group:
                        self._summaries[k] = _Summary(summary.flow, modules, reads)
            return flow
        finally:
            del self._active[key]
            previous_modules.update(self.source.touched)
            self.source.touched = previous_modules
            previous_reads.update(self.source.reads)
            self.source.reads = previous_reads
            previous_unstable.update(self.source.unstable_reads)
            for site, version in self.source.read_versions.items():
                if site in previous_versions and previous_versions[site] != version:
                    previous_unstable.add(site)
                else:
                    previous_versions.setdefault(site, version)
            self.source.read_versions, self.source.unstable_reads = (
                previous_versions,
                previous_unstable,
            )

    def _analyze_function(
        self,
        function: _Definition,
        stack: tuple[str, ...],
        *,
        iterated: bool,
        receiver: str | None = None,
        dependency_transport: str | None = None,
        bindings: _Environment | None = None,
    ) -> _Flow:
        node = function.node
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        flow = _Flow()
        for decorator in node.decorator_list:
            name = self.source.resolve(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                function.module,
            )
            if name == "abc.abstractmethod":
                return _Flow(
                    unknowns=[
                        _Unknown("abstract implementation", function.name, node.lineno)
                    ]
                )
            if name not in {
                "dishka.integrations.fastapi.inject",
                "builtins.staticmethod",
                "builtins.classmethod",
                "builtins.property",
                "functools.cached_property",
                "functools.cache",
                "functools.lru_cache",
                *_VALIDATORS,
                *_SERIALIZERS,
            } and not _operation_decorator(decorator):
                flow.unknowns.append(
                    _Unknown("unresolved decorator", function.name, decorator.lineno)
                )
        if not iterated and _generator(node):
            flow.unknowns.append(
                _Unknown(
                    "generator execution is outside the HTTP response boundary",
                    function.name,
                    node.lineno,
                )
            )
            return flow
        env = self.source.environment(
            function, receiver, dependency_transport, bindings=bindings
        )
        if bindings is None and dependency_transport is not None:
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]:
                if argument.annotation is None:
                    continue
                value = self.source.typed(argument.annotation, function.module)
                if value.dependency and value.name:
                    flow.extend(self._dependency(value.name, function, stack))
        flow.extend(self.block(node.body, function, env, (*stack, function.name)))
        flow.unknowns = [
            unknown
            for unknown in flow.unknowns
            if not (
                unknown.deferred
                and env.get(f"$awaited:{unknown.deferred}") == _Value("awaited")
            )
        ]
        return flow

    def _dependency(
        self,
        port: str,
        function: _Definition,
        stack: tuple[str, ...],
        pending: frozenset[str] = frozenset(),
    ) -> _Flow:
        flow = _Flow()
        if port in pending:
            flow.unknowns.append(
                _Unknown(
                    "dependency construction cycle", function.name, function.node.lineno
                )
            )
            return flow
        self.source.provided(port, implicit=True)
        for creation in self.source.creations.get(port, []):
            for dependency in creation.dependencies:
                flow.extend(
                    self._dependency(dependency, function, stack, pending | {port})
                )
            definition = self.source.definitions.get(creation.name)
            if definition is None:
                continue
            if isinstance(definition.node, ast.ClassDef):
                flow.extend(
                    self._construct(
                        definition,
                        definition,
                        stack,
                        definition.node.lineno,
                        call=creation.call,
                        env=dict(creation.bindings),
                        constructed=creation.instance,
                    )
                )
                continue
            for decorator in definition.node.decorator_list:
                name = self.source.resolve(
                    decorator.func if isinstance(decorator, ast.Call) else decorator,
                    definition.module,
                )
                if name not in {
                    "dishka.provide",
                    "dishka.provider.provide",
                    *_PLAIN_DECORATORS,
                }:
                    flow.unknowns.append(
                        _Unknown(
                            "unresolved dependency factory decorator",
                            definition.name,
                            decorator.lineno,
                        )
                    )
            body = definition.node.body
            phases = None
            if _generator(definition.node):
                phases = _phases(body)
                if phases is None:
                    flow.unknowns.append(
                        _Unknown(
                            "unresolved dependency resource entry",
                            definition.name,
                            definition.node.lineno,
                        )
                    )
                    continue
                body = phases.before
            local = self.source.environment(
                definition, bindings=dict(creation.bindings)
            )
            if phases:
                self._entering[definition.name] = frozenset(
                    (context.lineno, context.col_offset) for context in phases.contexts
                )
            try:
                flow.extend(
                    self.block(body, definition, local, (*stack, definition.name))
                )
                if phases and phases.yielded:
                    flow.extend(
                        self.expression(
                            phases.yielded, definition, local, (*stack, definition.name)
                        )
                    )
            finally:
                self._entering.pop(definition.name, None)
        return flow

    def expression(
        self,
        node: ast.expr,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        *,
        awaited: bool = False,
        iterated: bool = False,
    ) -> _Flow:
        flow = _Flow()
        if isinstance(node, ast.Await):
            deferred = self.source.coroutine(
                self.source.infer(node.value, function.module, env)
            )
            wrapper = isinstance(node.value, ast.Call) and self.source.resolve(
                node.value.func, function.module, env
            ) in {
                "asyncio.shield",
                "asyncio.tasks.shield",
                "asyncio.wait_for",
                "asyncio.tasks.wait_for",
            }
            sync_factory = False
            if isinstance(node.value, ast.Call):
                target = self.source.infer(node.value.func, function.module, env)
                definition = self.source.definitions.get(target.name)
                sync_factory = bool(
                    definition and isinstance(definition.node, ast.FunctionDef)
                )
            if (
                deferred is not None
                and deferred.name == "<coroutine>"
                and deferred.bound
                and (not isinstance(node.value, ast.Call) or wrapper or sync_factory)
            ):
                # Calling creates the coroutine; advancing it executes the body.
                flow = self.expression(node.value, function, env, stack)
                flow.extend(self._resume(deferred, function, env, stack, node.lineno))
                return flow
            if not isinstance(node.value, ast.Call) or sync_factory:
                flow.extend(self.expression(node.value, function, env, stack))
                flow.unknowns.append(
                    _Unknown(
                        "unresolved awaitable execution", function.name, node.lineno
                    )
                )
                return flow
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
                _Unknown(
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
            flow.extend(self.expression(node.value, function, env, stack))
            self.source.assign(
                node.target, self.source.infer(node.value, function.module, env), env
            )
            return flow
        if isinstance(node, ast.Call):
            flow.extend(self.expression(node.func, function, env, stack))
            origin = self.source.resolve(node.func, function.module, env)
            if (
                origin in {"asyncio.create_task", "asyncio.tasks.create_task"}
                and node.args
            ):
                for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                    flow.extend(self.expression(argument, function, env, stack))
                pending = self.source.coroutine(
                    self.source.infer(node.args[0], function.module, env)
                )
                if pending and pending.bound:
                    flow.unknowns = [
                        unknown
                        for unknown in flow.unknowns
                        if unknown.deferred != pending.site
                    ]
                    flow.scheduled.add(pending.bound.name)
                return flow
            if origin in {"asyncio.gather", "asyncio.tasks.gather"} and awaited:
                resumed = _Flow()
                for argument in node.args:
                    flow.extend(self.expression(argument, function, env, stack))
                    resumed.extend(
                        self._resume(
                            self.source.infer(argument, function.module, env),
                            function,
                            env,
                            stack,
                            node.lineno,
                        )
                    )
                if any(
                    kw.arg == "return_exceptions"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in node.keywords
                ):
                    resumed.escapes.clear()
                for keyword in node.keywords:
                    flow.extend(self.expression(keyword.value, function, env, stack))
                flow.extend(resumed)
                return flow
            if (
                origin in {"asyncio.to_thread", "asyncio.threads.to_thread"}
                and node.args
            ):
                for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                    flow.extend(self.expression(argument, function, env, stack))
                if awaited:
                    invocation = ast.copy_location(
                        ast.Call(
                            func=node.args[0],
                            args=node.args[1:],
                            keywords=node.keywords,
                        ),
                        node,
                    )
                    flow.extend(
                        self._invoke(
                            invocation,
                            self.source.infer(node.args[0], function.module, env),
                            function,
                            env,
                            stack,
                            awaited=False,
                            iterated=False,
                        )
                    )
                else:
                    flow.unknowns.append(
                        _Unknown(
                            "coroutine execution is deferred until awaited",
                            function.name,
                            node.lineno,
                            self.source.site(node, function.module, env, "deferred"),
                        )
                    )
                return flow
            target = self.source.infer(node.func, function.module, env)
            consumers = {
                "builtins.any",
                "builtins.all",
                "builtins.sum",
                "builtins.tuple",
                "builtins.list",
                "builtins.set",
                "builtins.dict",
                "builtins.min",
                "builtins.max",
                "builtins.sorted",
                "builtins.str.join",
            }
            for index, argument in enumerate(
                [*node.args, *(kw.value for kw in node.keywords)]
            ):
                flow.extend(
                    self.expression(
                        argument,
                        function,
                        env,
                        stack,
                        iterated=index == 0 and bool(target.name in consumers),
                    )
                )
            for variant in target.variants:
                flow.extend(
                    self._invoke(
                        node,
                        variant,
                        function,
                        env,
                        stack,
                        awaited=awaited,
                        iterated=iterated,
                    )
                )
            return flow
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                flow.extend(self.expression(child, function, env, stack))
        if isinstance(node, ast.Attribute):
            for receiver in self.source.infer(
                node.value, function.module, env
            ).variants:
                if receiver.instance and any(
                    self.source.method(receiver.name, lookup) is not None
                    for lookup in ("__getattribute__", "__getattr__")
                ):
                    flow.unknowns.append(
                        _Unknown(
                            "custom attribute lookup requires receiver effect analysis",
                            function.name,
                            node.lineno,
                        )
                    )
                member = self.source.method(receiver.name, node.attr)
                if (
                    member
                    and isinstance(member.node, ast.FunctionDef)
                    and any(
                        self.source.resolve(decorator, member.module)
                        in {"builtins.property", "functools.cached_property"}
                        for decorator in member.node.decorator_list
                    )
                ):
                    positional = [*member.node.args.posonlyargs, *member.node.args.args]
                    flow.extend(
                        self._traced(
                            function,
                            self.function(
                                member,
                                stack,
                                bindings={positional[0].arg: receiver}
                                if positional
                                else {},
                            ),
                        )
                    )
        return flow

    def _exit_stack(
        self,
        node: ast.Call,
        target: _Value,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
    ) -> _Flow:
        flow = _Flow()
        assert target.bound is not None
        receiver = self.source.object(target.bound)
        method = target.name.rsplit(".", 1)[-1]
        if not receiver.site or method == "pop_all":
            return _Flow(
                unknowns=[
                    _Unknown(
                        "exit stack callbacks require resource ownership analysis",
                        function.name,
                        node.lineno,
                    )
                ]
            )
        callbacks = dict(receiver.fields).get("$cleanup", _Value("<empty>"))
        if method in {"enter_context", "enter_async_context"} and node.args:
            resource = self.source.infer(node.args[0], function.module, env)
            entered, cleanup = self._enter_resource(
                resource, function, env, stack, node.lineno
            )
            flow.extend(entered)
            env[f"$cleanup-must:{receiver.site}:{cleanup.site}"] = _Value("registered")
            fields = dict(receiver.fields)
            fields["$cleanup"] = _join(callbacks, cleanup)
            self.source.store(replace(receiver, fields=tuple(sorted(fields.items()))))
            return flow
        if method in {"callback", "push_async_callback"} and node.args:
            invocation = ast.copy_location(
                ast.Call(func=node.args[0], args=node.args[1:], keywords=node.keywords),
                node,
            )
            callback = self.source.infer(node.args[0], function.module, env)
            definition = self.source.definitions.get(callback.name)
            bindings = (
                self.source.call_environment(
                    definition, invocation, function.module, env, callback.bound
                )
                if definition
                else None
            )
            cleanup = _Value(
                "<callback>",
                bound=callback,
                fields=tuple(sorted((bindings or {}).items())),
                unresolved=definition is None,
                site=self.source.site(node, function.module, env, "cleanup"),
            )
            env[f"$cleanup-must:{receiver.site}:{cleanup.site}"] = _Value("registered")
            fields = dict(receiver.fields)
            fields["$cleanup"] = _join(callbacks, cleanup)
            self.source.store(replace(receiver, fields=tuple(sorted(fields.items()))))
            return flow
        if method in {"close", "aclose", "__exit__", "__aexit__"}:
            if receiver.site in self._closing:
                return _Flow(
                    unknowns=[
                        _Unknown(
                            "reentrant owned resource cleanup is unresolved",
                            function.name,
                            node.lineno,
                        )
                    ]
                )
            cleanup_flow = _Flow()
            for callback in reversed(callbacks.variants):
                if callback.name == "<empty>":
                    continue
                if (
                    callback.name == "<continuation>"
                    and callback.site in self._continuations
                ):
                    manager, body = self._continuations[callback.site]
                    previous = self._yields.get(manager.name)
                    self._yields[manager.name] = cleanup_flow
                    self._closing.add(receiver.site)
                    try:
                        executed = self.block(
                            body, manager, dict(callback.fields), (*stack, manager.name)
                        )
                    finally:
                        self._closing.remove(receiver.site)
                        if previous is not None:
                            self._yields[manager.name] = previous
                        else:
                            self._yields.pop(manager.name, None)
                    executed.escapes = [
                        replace(
                            escape,
                            trace=(function.name, *escape.trace)
                            if escape.trace[0] == manager.name
                            else escape.trace,
                        )
                        for escape in executed.escapes
                    ]
                    if env.get(
                        f"$cleanup-must:{receiver.site}:{callback.site}"
                    ) == _Value("registered"):
                        cleanup_flow = executed
                    else:
                        cleanup_flow.extend(executed)
                elif (
                    callback.name == "<callback>"
                    and callback.bound
                    and not callback.unresolved
                ):
                    definition = self.source.definitions[callback.bound.name]
                    cleanup_flow.extend(
                        self._traced(
                            function,
                            self.function(
                                definition, stack, bindings=dict(callback.fields)
                            ),
                        )
                    )
                elif callback.name != "<external-cleanup>":
                    cleanup_flow.unknowns.append(
                        _Unknown(
                            "unresolved owned resource cleanup",
                            function.name,
                            node.lineno,
                        )
                    )
            flow.extend(cleanup_flow)
            return flow
        return _Flow(
            unknowns=[
                _Unknown(
                    "exit stack callbacks require resource ownership analysis",
                    function.name,
                    node.lineno,
                )
            ]
        )

    def _enter_resource(
        self,
        resource: _Value,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        line: int,
    ) -> tuple[_Flow, _Value]:
        if resource.name == "<contextmanager>" and resource.bound:
            manager = self.source.definitions.get(resource.bound.name)
            if manager and isinstance(
                manager.node, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                phases = _phases(manager.node.body)
                if phases and not phases.contexts and manager.name not in stack:
                    local = self.source.environment(
                        manager, bindings=dict(resource.fields)
                    )
                    entry = self.block(
                        phases.before, manager, local, (*stack, manager.name)
                    )
                    self._continuations[resource.site] = manager, phases.after
                    cleanup = _Value(
                        "<continuation>",
                        bound=resource.bound,
                        site=resource.site,
                        fields=tuple(sorted(local.items())),
                    )
                    return self._traced(function, entry), cleanup
        if (
            resource.exact
            and dict(resource.fields).get("$resource-defaults", _Value()).literal
            is True
            and self.source.external(resource.name)
            and self.source.libraries.canonical(resource.name)
            in {"cdpify.client.CdpClient", "httpx2._client.AsyncClient"}
        ):
            return _Flow(), _Value("<external-cleanup>")
        return _Flow(
            unknowns=[
                _Unknown("unresolved owned context manager entry", function.name, line)
            ]
        ), _Value("<unknown-cleanup>")

    def _resume(
        self,
        deferred: _Value,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        line: int,
    ) -> _Flow:
        flow = _Flow()
        for value in deferred.variants:
            value = self.source.coroutine(value) or value
            definition = (
                self.source.definitions.get(value.bound.name) if value.bound else None
            )
            if value.name != "<coroutine>" or definition is None:
                flow.unknowns.append(
                    _Unknown("unresolved awaitable execution", function.name, line)
                )
                continue
            env[f"$awaited:{value.site}"] = _Value("awaited")
            flow.extend(
                self._traced(
                    function,
                    self.function(
                        definition,
                        stack,
                        receiver=value.bound.receiver if value.bound else None,
                        bindings=dict(value.fields),
                    ),
                )
            )
        return flow

    def _invoke(
        self,
        node: ast.Call,
        target: _Value,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        *,
        awaited: bool,
        iterated: bool,
    ) -> _Flow:
        flow = _Flow()
        if target.alternatives:
            for variant in target.variants:
                flow.extend(
                    self._invoke(
                        node,
                        variant,
                        function,
                        env,
                        stack,
                        awaited=awaited,
                        iterated=iterated,
                    )
                )
            return flow
        if target.bound and target.bound.name in {
            "contextlib.ExitStack",
            "contextlib.AsyncExitStack",
        }:
            return self._exit_stack(node, target, function, env, stack)
        name = target.name or self.source.resolve(node.func, function.module, env)
        if name == "<empty>":
            return flow
        if name == "builtins.super":
            # Proxy construction adds no application errors. Member lookup below
            # follows the receiver's MRO and analyzes the selected own method.
            return flow
        definition = self.source.definitions.get(name or "")
        if name == "builtins.type" and len(node.args) == 1 and not node.keywords:
            return flow
        if (
            name == "builtins.getattr"
            and len(node.args) in {2, 3}
            and not node.keywords
        ):
            names = self.source.infer(node.args[1], function.module, env)
            if all(isinstance(value.literal, str) for value in names.variants):
                for value in names.variants:
                    access = ast.copy_location(
                        ast.Attribute(
                            value=node.args[0], attr=str(value.literal), ctx=ast.Load()
                        ),
                        node,
                    )
                    flow.extend(self.expression(access, function, env, stack))
                if len(node.args) == 3:
                    flow.escapes = [
                        escape
                        for escape in flow.escapes
                        if "builtins.AttributeError"
                        not in (self.source.mro(escape.exception) or ())
                    ]
                return flow
        if name in {"builtins.iter", "builtins.next"} and node.args:
            value = self.source.infer(node.args[0], function.module, env)
            for variant in value.variants:
                if variant.name in {
                    "builtins.dict",
                    "builtins.list",
                    "builtins.tuple",
                    "builtins.set",
                    "builtins.frozenset",
                    "builtins.str",
                    "collections.abc.Iterator",
                    "collections.abc.Sequence",
                    "collections.abc.Iterable",
                }:
                    continue
                flow.unknowns.append(
                    _Unknown(
                        "unresolved iteration protocol", function.name, node.lineno
                    )
                )
            return flow
        if target.unresolved and (
            definition is None or not self.source.abstract(definition)
        ):
            flow.unknowns.append(
                _Unknown(
                    "unresolved Dishka value at call site", function.name, node.lineno
                )
            )
        if name and name.startswith("builtins.") and self.source.exception(name):
            return flow
        if definition and isinstance(definition.node, ast.ClassDef):
            if target.instance:
                method = self.source.method(definition.name, "__call__")
                if method is None:
                    flow.unknowns.append(
                        _Unknown(
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
                            bindings=self.source.call_environment(
                                method, node, function.module, env, target
                            ),
                        )
                    )
            else:
                flow.extend(
                    self._construct(
                        definition, function, stack, node.lineno, call=node, env=env
                    )
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
            if (
                definition.owner
                and isinstance(node.func, ast.Attribute)
                and not (target.bound and target.bound.exact)
            ):
                receiver = self.source.resolve(node.func.value, function.module, env)
                if (
                    receiver
                    and receiver != definition.owner
                    and not self.source.classmethod(definition)
                ):
                    flow.unknowns.append(
                        _Unknown(
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
                    bindings=self.source.call_environment(
                        definition,
                        node,
                        function.module,
                        env,
                        target.bound,
                        captures=target.fields,
                    ),
                    deferred=self.source.site(node, function.module, env, "deferred"),
                )
            )
        elif (
            origin := self.source.call_origin(target)
            or self.source.origin(node.func, function.module, env)
        ) is not None:
            if target.bound and self.source.model(target.bound.name):
                model_method = origin.rsplit(".", 1)[-1]
                definition = self.source.definitions.get(target.bound.name)
                if definition and model_method in {
                    "model_validate",
                    "model_validate_json",
                    "model_validate_strings",
                }:
                    flow.extend(
                        self._construct(definition, function, stack, node.lineno)
                    )
                if definition and model_method in {"model_dump", "model_dump_json"}:
                    flow.extend(
                        self._model_hooks(
                            definition.name, target.bound, function, stack, _SERIALIZERS
                        )
                    )
            # Library code raises no application exceptions of its own; it
            # reaches them only through receivers and callables it is given.
            if origin in _STACK_EFFECTS or (
                target.bound
                and target.bound.name
                in {"contextlib.ExitStack", "contextlib.AsyncExitStack"}
                and origin.rsplit(".", 1)[-1]
                in {name.rsplit(".", 1)[-1] for name in _STACK_EFFECTS}
            ):
                flow.unknowns.append(
                    _Unknown(
                        "exit stack callbacks require resource ownership analysis",
                        function.name,
                        node.lineno,
                    )
                )
            for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                flow.extend(self._callback(argument, function, env, stack))
        else:
            flow.unknowns.append(
                _Unknown(
                    f"unresolved call: {ast.unparse(node.func)}",
                    function.name,
                    node.lineno,
                )
            )
        return flow

    def _comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
    ) -> _Flow:
        flow = _Flow()
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
                    _Unknown(
                        "unsupported comprehension target",
                        function.name,
                        generator.target.lineno,
                    )
                )
        for index, generator in enumerate(node.generators):
            iteration_env = env if index == 0 else local
            flow.extend(
                self._comprehension_iterable(
                    generator.iter,
                    function,
                    iteration_env,
                    stack,
                    asynchronous=bool(generator.is_async),
                )
            )
            element = self.source.element(
                self.source.infer(generator.iter, function.module, iteration_env)
            )
            if element.name == "<empty>":
                return flow
            self.source.assign(
                generator.target,
                self.source.element(
                    self.source.infer(generator.iter, function.module, iteration_env)
                ),
                local,
            )
            for condition in generator.ifs:
                flow.extend(self.expression(condition, function, local, stack))
        values = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        for value in values:
            flow.extend(self.expression(value, function, local, stack))
        for child in ast.walk(node):
            if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
                name = child.target.id
                env[name] = _join(env.get(name, _Value()), local.get(name, _Value()))
        return flow

    def _comprehension_iterable(
        self,
        node: ast.expr,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        *,
        asynchronous: bool,
    ) -> _Flow:
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
                _Unknown(
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
                _Unknown(
                    "unresolved async comprehension iterator",
                    function.name,
                    node.lineno,
                )
            )
        return flow

    def _context_manager(
        self,
        node: ast.With | ast.AsyncWith,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        reraised: tuple[_Escape, ...],
        index: int = 0,
        states: list[_Environment] | None = None,
    ) -> _Flow:
        if index == len(node.items):
            return self.block(node.body, function, env, stack, reraised, states=states)
        item = node.items[index]
        if (node.lineno, node.col_offset) in self._entering.get(function.name, ()):
            flow = self.expression(item.context_expr, function, env, stack)
            resource = self.source.infer(item.context_expr, function.module, env)
            entered = []
            for variant in resource.variants:
                if variant.name in _PLAIN_CONTEXTS and not variant.unresolved:
                    entry = _Flow()
                else:
                    entry, _ = self._enter_resource(
                        variant, function, env, stack, item.context_expr.lineno
                    )
                flow.extend(entry)
                entry_method = self.source.member(
                    variant,
                    "__aenter__" if isinstance(node, ast.AsyncWith) else "__enter__",
                )
                entered.append(
                    self.source.libraries.returned(
                        entry_method, awaited=isinstance(node, ast.AsyncWith)
                    )
                    or _Value()
                )
            if item.optional_vars:
                self.source.assign(item.optional_vars, _join(*entered), env)
            flow.extend(
                self._context_manager(
                    node, function, env, stack, reraised, index + 1, states=states
                )
            )
            return flow
        if isinstance(item.context_expr, ast.Call):
            target = self.source.infer(item.context_expr.func, function.module, env)
            definition = self.source.definitions.get(target.name)
            if definition and isinstance(
                definition.node, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                decorators = {
                    self.source.resolve(d, definition.module)
                    for d in definition.node.decorator_list
                }
                expected = (
                    "contextlib.asynccontextmanager"
                    if isinstance(node, ast.AsyncWith)
                    else "contextlib.contextmanager"
                )
                if decorators == {expected} and _generator(definition.node):
                    return self._generator_context(
                        node,
                        function,
                        env,
                        stack,
                        reraised,
                        index,
                        definition,
                        target,
                        states=states,
                    )
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
                node, function, local, stack, reraised, index + 1, states=states
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
                    _Unknown(
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
            self.source.merge_environments(env, [env.copy(), local])
            return flow
        manager = self._context_type(item.context_expr, function, env)
        asynchronous = isinstance(node, ast.AsyncWith)
        manager_value = self.source.infer(item.context_expr, function.module, env)
        if all(
            value.name in _PLAIN_CONTEXTS and self.source.external(value.name)
            for value in manager_value.variants
        ):
            local = env.copy()
            if item.optional_vars is not None:
                self.source.assign(item.optional_vars, manager_value, local)
            flow.extend(
                self._context_manager(
                    node, function, local, stack, reraised, index + 1, states=states
                )
            )
            env.update(local)
            return flow
        if (
            isinstance(item.context_expr, ast.Call)
            and self.source.resolve(item.context_expr.func, function.module, env)
            == "contextlib.closing"
            and len(item.context_expr.args) == 1
        ):
            resource = self.source.infer(
                item.context_expr.args[0], function.module, env
            )
            local = env.copy()
            if item.optional_vars:
                self.source.assign(item.optional_vars, resource, local)
            inner = self._context_manager(
                node, function, local, stack, reraised, index + 1, states=states
            )
            flow.extend(inner)
            invocation = ast.copy_location(
                ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="$closed", ctx=ast.Load()),
                        attr="close",
                        ctx=ast.Load(),
                    ),
                    args=[],
                    keywords=[],
                ),
                item.context_expr,
            )
            for value in resource.variants:
                flow.extend(
                    self._invoke(
                        invocation,
                        self.source.member(value, "close"),
                        function,
                        {**env, "$closed": value},
                        stack,
                        awaited=False,
                        iterated=False,
                    )
                )
            env.update(local)
            return flow
        # These managers neither invoke application hooks nor suppress their errors.
        manager_type = self.source.infer(item.context_expr, function.module, env).name
        if (
            asynchronous
            and manager_type
            in {
                "asyncio.Lock",
                "asyncio.locks.Lock",
                "asyncio.Timeout",
                "asyncio.timeouts.Timeout",
            }
            and self.source.external(manager_type)
        ):
            local = env.copy()
            if item.optional_vars is not None:
                self._invalidate(item.optional_vars, local)
            flow.extend(
                self._context_manager(
                    node, function, local, stack, reraised, index + 1, states=states
                )
            )
            env.update(local)
            return flow
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
                _Unknown(
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
                local[item.optional_vars.id] = _Value(returned or "", instance=True)
            elif not isinstance(item.optional_vars, ast.Name):
                flow.unknowns.append(
                    _Unknown(
                        "unsupported context manager binding target",
                        function.name,
                        item.optional_vars.lineno,
                    )
                )
        inner = self._context_manager(
            node, function, local, stack, reraised, index + 1, states=states
        )
        if supported:
            assert exit_method is not None
            mode = _suppression(exit_method.node)
            if mode is None:
                flow.unknowns.append(
                    _Unknown(
                        "context manager exception suppression is unresolved",
                        function.name,
                        item.context_expr.lineno,
                    )
                )
            if mode is True or _always_raises(exit_method.node):
                inner.escapes.clear()
            flow.extend(inner)
            flow.extend(self._traced(function, self.function(exit_method, stack)))
            if mode is False:
                env.update(local)
            else:
                self.source.merge_environments(env, [env.copy(), local])
        else:
            flow.extend(inner)
            self.source.merge_environments(env, [env.copy(), local])
        return flow

    def _generator_context(
        self,
        node: ast.With | ast.AsyncWith,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        reraised: tuple[_Escape, ...],
        index: int,
        manager: _Definition,
        target: _Value,
        states: list[_Environment] | None = None,
    ) -> _Flow:
        item = node.items[index]
        assert isinstance(item.context_expr, ast.Call)
        assert isinstance(manager.node, ast.FunctionDef | ast.AsyncFunctionDef)
        flow = _Flow()
        for argument in [
            *item.context_expr.args,
            *(kw.value for kw in item.context_expr.keywords),
        ]:
            flow.extend(self.expression(argument, function, env, stack))
        bindings = self.source.call_environment(
            manager, item.context_expr, function.module, env, target.bound
        )
        yields = [
            child
            for child in _executed_nodes(manager.node.body)
            if isinstance(child, ast.Yield | ast.YieldFrom)
        ]
        if (
            bindings is None
            or len(yields) != 1
            or isinstance(yields[0], ast.YieldFrom)
            or manager.name in stack
        ):
            flow.unknowns.append(
                _Unknown(
                    "unresolved generator context manager execution",
                    function.name,
                    item.context_expr.lineno,
                )
            )
            flow.extend(
                self._context_manager(
                    node,
                    function,
                    env.copy(),
                    stack,
                    reraised,
                    index + 1,
                    states=states,
                )
            )
            return flow
        local = env.copy()
        if item.optional_vars is not None:
            self.source.assign(
                item.optional_vars, self.source.returned(manager, bindings), local
            )
        inner = self._context_manager(
            node, function, local, stack, reraised, index + 1, states=states
        )
        manager_env = self.source.environment(
            manager, target.receiver, bindings=bindings
        )
        self._yields[manager.name] = inner
        try:
            executed = self.block(
                manager.node.body, manager, manager_env, (*stack, manager.name)
            )
        finally:
            del self._yields[manager.name]
        flow.escapes.extend(
            replace(
                escape,
                trace=(function.name, *escape.trace)
                if escape.trace[0] == manager.name
                else escape.trace,
            )
            for escape in executed.escapes
        )
        flow.unknowns.extend(executed.unknowns)
        flow.scheduled.update(executed.scheduled)
        may_suppress = any(
            isinstance(child, ast.Try)
            and _has_yield(child)
            and any(
                not self.source.terminates(handler.body, manager.module, manager_env)
                for handler in child.handlers
            )
            for child in _executed_nodes(manager.node.body)
        )
        if may_suppress:
            self.source.merge_environments(env, [env.copy(), local])
        else:
            env.update(local)
        return flow

    def _context_type(
        self, node: ast.expr, function: _Definition, env: _Environment
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
        definition: _Definition,
        function: _Definition,
        stack: tuple[str, ...],
        line: int,
        *,
        call: ast.Call | None = None,
        env: _Environment | None = None,
        constructed: _Value | None = None,
    ) -> _Flow:
        """Follow the application code that constructing *definition* runs."""
        flow = _Flow()
        lineage = self.source.lineage(definition.name)
        if lineage is None:
            flow.unknowns.append(
                _Unknown(
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
                    _Unknown(
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
                _Unknown(
                    "class decorators or metaclasses may change construction",
                    function.name,
                    line,
                )
            )
        hooks = ["__new__", "__init__"]
        if self.source.method(definition.name, "__new__") is not None:
            flow.unknowns.append(
                _Unknown(
                    "custom allocation may return a different implementation",
                    function.name,
                    line,
                )
            )
        if (
            self.source.method(definition.name, "__getattribute__") is not None
            or self.source.method(definition.name, "__getattr__") is not None
        ):
            flow.unknowns.append(
                _Unknown(
                    "custom attribute lookup requires receiver effect analysis",
                    function.name,
                    line,
                )
            )
        instance = constructed
        if instance is None and call is not None:
            instance = self.source.construct(
                definition.name, call, function.module, env or {}
            )
        if external and self.source.method(definition.name, "__init__") is None:
            for owner in reversed(classes):
                owner_definition = self.source.definitions[owner]
                assert isinstance(owner_definition.node, ast.ClassDef)
                for field_node in owner_definition.node.body:
                    if not (
                        isinstance(field_node, ast.AnnAssign)
                        and isinstance(field_node.target, ast.Name)
                        and isinstance(field_node.value, ast.Call)
                        and self.source.resolve(
                            field_node.value.func, owner_definition.module
                        )
                        in {"dataclasses.field", "pydantic.Field"}
                    ):
                        continue
                    factory = next(
                        (
                            kw.value
                            for kw in field_node.value.keywords
                            if kw.arg == "default_factory"
                        ),
                        None,
                    )
                    if factory is None:
                        continue
                    # Explicit arguments skip the factory; unknown unpacking may
                    # omit the field, so both paths must remain in the summary.
                    supplied = call is not None and any(
                        kw.arg == field_node.target.id for kw in call.keywords
                    )
                    if supplied:
                        continue
                    target = self.source.infer(factory, owner_definition.module, {})
                    invocation = ast.copy_location(
                        ast.Call(
                            func=ast.Name(id="$factory", ctx=ast.Load()),
                            args=[],
                            keywords=[],
                        ),
                        call or field_node,
                    )
                    flow.extend(
                        self._invoke(
                            invocation,
                            target,
                            function,
                            {"$factory": target},
                            stack,
                            awaited=False,
                            iterated=False,
                        )
                    )
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
                assert isinstance(member.node, ast.FunctionDef | ast.AsyncFunctionDef)
                parameters = [
                    *member.node.args.posonlyargs,
                    *member.node.args.args,
                    *member.node.args.kwonlyargs,
                    *filter(None, [member.node.args.vararg, member.node.args.kwarg]),
                ]
                # A class passed as a library callback has no known invocation
                # arguments. Its annotations cannot supply those runtime values.
                bindings = {parameter.arg: _Value() for parameter in parameters}
                if parameters:
                    bindings[parameters[0].arg] = (
                        _Value(definition.name, exact=True)
                        if hook == "__new__"
                        else instance or _Value(definition.name, instance=True)
                    )
                if instance is not None and call is not None:
                    bound = (
                        _Value(definition.name, exact=True)
                        if hook == "__new__"
                        else instance
                    )
                    if hook in {"__new__", "__init__"}:
                        call_bindings = self.source.call_environment(
                            member, call, function.module, env or {}, bound
                        )
                        if call_bindings is None:
                            flow.unknowns.append(
                                _Unknown(
                                    "unresolved constructor arguments",
                                    function.name,
                                    line,
                                )
                            )
                        else:
                            bindings = call_bindings
                    else:
                        assert isinstance(
                            member.node, ast.FunctionDef | ast.AsyncFunctionDef
                        )
                        first = [*member.node.args.posonlyargs, *member.node.args.args]
                        bindings = {first[0].arg: bound} if first else {}
                flow.extend(
                    self._traced(
                        function,
                        self.function(
                            member, stack, receiver=definition.name, bindings=bindings
                        ),
                    )
                )
        if external:
            if self.source.model(definition.name):
                if self.source.models.opaque(definition.name):
                    flow.unknowns.append(
                        _Unknown(
                            "before, plain or wrap validation requires value transformation analysis",
                            function.name,
                            line,
                        )
                    )
                flow.extend(
                    self._model_hooks(
                        definition.name, instance, function, stack, _VALIDATORS
                    )
                )
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
                        and not (
                            self.source.model(definition.name)
                            and self.source.resolve(
                                item.func if isinstance(item, ast.Call) else item,
                                self.source.definitions[owner].module,
                            )
                            in _VALIDATORS | _SERIALIZERS
                        )
                        for item in member_node.decorator_list
                    ):
                        flow.unknowns.append(
                            _Unknown(
                                "library construction may call decorated hook: "
                                f"{owner}.{member_node.name}",
                                function.name,
                                line,
                            )
                        )
        return flow

    def _model_hooks(
        self,
        owner: str,
        instance: _Value | None,
        function: _Definition,
        stack: tuple[str, ...],
        decorators: frozenset[str],
    ) -> _Flow:
        flow = _Flow()
        for name in (self.source.lineage(owner) or ([owner], False))[0]:
            definition = self.source.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for node in definition.node.body:
                if not isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ) or not any(
                    self.source.resolve(
                        d.func if isinstance(d, ast.Call) else d, definition.module
                    )
                    in decorators
                    for d in node.decorator_list
                ):
                    continue
                hook = self.source.definitions[f"{name}.{node.name}"]
                parameters = [*node.args.posonlyargs, *node.args.args]
                bindings = {
                    key: value
                    for key, value in self.source.environment(hook, owner).items()
                    if not key.startswith("$")
                }
                if parameters:
                    bindings[parameters[0].arg] = (
                        _Value(owner, exact=True)
                        if self.source.classmethod(hook)
                        else instance or _Value(owner, instance=True)
                    )
                alternatives = [bindings]
                if decorators == _VALIDATORS:
                    alternatives = [
                        arguments
                        for hook_name, decorator in self.source.models.hooks(owner)
                        if hook_name == hook.name
                        for _, arguments in self.source.models.hook_arguments(
                            instance or _Value(owner, instance=True),
                            hook_name,
                            decorator,
                        )
                    ]
                effects = _Flow()
                for arguments in alternatives:
                    effects.extend(
                        self.function(hook, stack, receiver=owner, bindings=arguments)
                    )
                if decorators == _VALIDATORS:
                    # Pydantic converts these failures to its ValidationError;
                    # other application exception classes propagate unchanged.
                    effects.escapes = [
                        escape
                        for escape in effects.escapes
                        if not any(
                            base in {"builtins.ValueError", "builtins.AssertionError"}
                            for base in self.source.mro(escape.exception) or ()
                        )
                    ]
                elif decorators == _SERIALIZERS:
                    # Pydantic wraps serializer failures; the original domain
                    # exception does not escape the serialization boundary.
                    effects.escapes = [
                        replace(
                            escape,
                            exception="pydantic_core.PydanticSerializationError",
                        )
                        for escape in effects.escapes
                    ]
                flow.extend(self._traced(function, effects))
        return flow

    def _callback(
        self,
        argument: ast.expr,
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
    ) -> _Flow:
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
                lambda_env[item.arg] = _Value()
            return self.expression(argument.body, function, lambda_env, stack)
        if not isinstance(argument, ast.Name | ast.Attribute | ast.Call):
            return _Flow()
        inferred = self.source.infer(argument, function.module, env)
        if inferred.alternatives:
            flow = _Flow()
            for variant in inferred.variants:
                local = {**env, "$callback": variant}
                flow.extend(
                    self._callback(
                        ast.copy_location(
                            ast.Name(id="$callback", ctx=ast.Load()), argument
                        ),
                        function,
                        local,
                        stack,
                    )
                )
            return flow
        if inferred.name == "<callable>":
            return _Flow(
                unknowns=[
                    _Unknown(
                        "unresolved application callback",
                        function.name,
                        argument.lineno,
                    )
                ]
            )
        name = inferred.name or self.source.resolve(argument, function.module, env)
        definition = self.source.definitions.get(name or "")
        if definition is None:
            return _Flow()
        if isinstance(definition.node, ast.ClassDef):
            # Typed parameters and attributes resolve to their class; they
            # pass an instance, not a constructor.
            if inferred.instance:
                method = self.source.method(definition.name, "__call__")
                if method is not None and not self.source.abstract(method):
                    assert isinstance(
                        method.node, ast.FunctionDef | ast.AsyncFunctionDef
                    )
                    callback_args = [
                        *method.node.args.posonlyargs,
                        *method.node.args.args,
                        *method.node.args.kwonlyargs,
                    ]
                    bindings = {parameter.arg: _Value() for parameter in callback_args}
                    if callback_args:
                        bindings[callback_args[0].arg] = inferred
                    return self._traced(
                        function,
                        self.function(
                            method, stack, receiver=definition.name, bindings=bindings
                        ),
                    )
                return (
                    _Flow()
                    if method is None
                    else _Flow(
                        unknowns=[
                            _Unknown(
                                "unresolved application callback",
                                function.name,
                                argument.lineno,
                            )
                        ]
                    )
                )
            return self._construct(definition, function, stack, argument.lineno)
        callback_parameters = [
            *definition.node.args.posonlyargs,
            *definition.node.args.args,
            *definition.node.args.kwonlyargs,
        ]
        bindings = {
            **dict(inferred.fields),
            **{parameter.arg: _Value() for parameter in callback_parameters},
        }
        if callback_parameters and inferred.bound:
            bindings[callback_parameters[0].arg] = inferred.bound
        return self._traced(
            function,
            self.function(
                definition,
                stack,
                iterated=_generator(definition.node),
                receiver=inferred.receiver,
                bindings=bindings,
            ),
        )

    def _traced(self, function: _Definition, called: _Flow) -> _Flow:
        return _Flow(
            [
                replace(item, trace=(function.name, *item.trace))
                for item in called.escapes
            ],
            list(called.unknowns),
            set(called.scheduled),
        )

    def _call(
        self,
        node: ast.Call,
        definition: _Definition,
        function: _Definition,
        stack: tuple[str, ...],
        *,
        awaited: bool,
        iterated: bool,
        receiver: str | None = None,
        bindings: _Environment | None = None,
        deferred: str = "",
    ) -> _Flow:
        flow = _Flow()
        iterated_generator = iterated and _generator(definition.node)
        assert isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
        decorators = {
            self.source.resolve(d, definition.module)
            for d in definition.node.decorator_list
        }
        if decorators in (
            {"contextlib.contextmanager"},
            {"contextlib.asynccontextmanager"},
        ):
            return flow
        if (
            isinstance(definition.node, ast.AsyncFunctionDef)
            and not awaited
            and not iterated_generator
        ):
            flow.unknowns.append(
                _Unknown(
                    "coroutine execution is deferred until awaited",
                    function.name,
                    node.lineno,
                    deferred,
                )
            )
            return flow
        called = self.function(
            definition,
            stack,
            iterated=iterated_generator,
            receiver=receiver,
            bindings=bindings,
        )
        flow.escapes.extend(
            replace(item, trace=(function.name, *item.trace)) for item in called.escapes
        )
        flow.unknowns.extend(called.unknowns)
        flow.scheduled.update(called.scheduled)
        return flow

    def _dispatch(
        self,
        node: ast.Call,
        port: _Definition,
        function: _Definition,
        stack: tuple[str, ...],
        *,
        awaited: bool,
        iterated: bool,
    ) -> _Flow:
        """Follow an abstract call to the implementations that can receive it.

        Dishka providers in the source bind ports to implementations. Abstract
        calls on ``self`` without a binding dispatch to concrete subclasses.
        Multiple candidates contribute the union of their escapes.
        """
        assert port.owner is not None
        assert isinstance(port.node, ast.FunctionDef | ast.AsyncFunctionDef)
        flow = _Flow()
        bound = self.source.implementations(port.owner)
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        candidates: list[_Definition] = []
        if bound:
            for implementation in bound:
                method = (
                    self.source.method(implementation, port.node.name)
                    if implementation
                    else None
                )
                if method is None or self.source.abstract(method):
                    flow.unknowns.append(
                        _Unknown(
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
                _Unknown(
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
        function: _Definition,
        env: _Environment,
        stack: tuple[str, ...],
        reraised: tuple[_Escape, ...] = (),
        states: list[_Environment] | None = None,
    ) -> _Flow:
        flow = _Flow()
        for node in body:
            if states is not None:
                states.append(
                    {key: value for key, value in env.items() if key != "$terminated"}
                )
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                env[node.name] = (
                    self.source.local_definition(node, function.module, env)
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    else _Value()
                )
                for expression in node.decorator_list:
                    flow.extend(self.expression(expression, function, env, stack))
                if isinstance(node, ast.ClassDef):
                    flow.unknowns.append(
                        _Unknown(
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
                            _Unknown("unresolved re-raise", function.name, node.lineno)
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
                            _Unknown(
                                f"unresolved raised exception: {ast.unparse(node.exc)}",
                                function.name,
                                node.lineno,
                            )
                        )
                env["$terminated"] = _Value("raise")
                break
            if isinstance(node, ast.Try):
                success_env = env.copy()
                exception_states: list[_Environment] = []
                attempted = self.block(
                    node.body,
                    function,
                    success_env,
                    stack,
                    reraised,
                    states=exception_states,
                )
                if states is not None:
                    states.extend(exception_states)
                handler_entry = env.copy()
                self.source.merge_environments(handler_entry, exception_states)
                continuing = []
                flow.unknowns.extend(attempted.unknowns)
                flow.scheduled.update(attempted.scheduled)
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
                            _Unknown(
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
                    handler_env = handler_entry.copy()
                    caught_types = {item.exception for item in caught}
                    if handler.name:
                        handler_env[handler.name] = _Value(
                            next(iter(caught_types)) if len(caught_types) == 1 else "",
                            instance=True,
                        )
                    # Implicit failures (e.g. int(value)) can reach handlers too.
                    implicit = [
                        _Escape(name, (function.name,), handler.lineno, implicit=True)
                        for name in (
                            names
                            or (
                                ["builtins.BaseException"]
                                if handler.type is None
                                else []
                            )
                        )
                        if name
                        and (name.startswith("builtins.") or self.source.external(name))
                        and self.source.mro(name)
                    ]
                    if (
                        not caught
                        and not implicit
                        and not unknown_catch
                        and not attempted.unknowns
                    ):
                        continue
                    flow.extend(
                        self.block(
                            handler.body,
                            function,
                            handler_env,
                            stack,
                            tuple([*caught, *implicit]),
                            states=states,
                        )
                    )
                    if "$terminated" not in handler_env:
                        continuing.append(handler_env)
                flow.escapes.extend(remaining)
                if "$terminated" not in success_env:
                    flow.extend(
                        self.block(
                            node.orelse,
                            function,
                            success_env,
                            stack,
                            reraised,
                            states=states,
                        )
                    )
                if "$terminated" not in success_env:
                    continuing.append(success_env)
                self.source.merge_environments(env, continuing or [handler_entry])
                env.pop("$terminated", None)
                flow.extend(
                    self.block(
                        node.finalbody, function, env, stack, reraised, states=states
                    )
                )
                if any(
                    isinstance(child, ast.Return | ast.Raise)
                    for child in _executed_nodes(node.finalbody)
                ):
                    flow.unknowns.append(
                        _Unknown(
                            "finally may replace an escaping exception",
                            function.name,
                            node.lineno,
                        )
                    )
                if not continuing or "$terminated" in env:
                    env["$terminated"] = _Value("terminated")
                    break
                continue
            if isinstance(node, ast.With | ast.AsyncWith):
                flow.extend(
                    self._context_manager(
                        node, function, env, stack, reraised, states=states
                    )
                )
                continue
            if isinstance(node, ast.If):
                flow.extend(self.expression(node.test, function, env, stack))
                condition = self.source.condition(node.test, function.module, env)
                left = self.source.narrow(node.test, function.module, env, True)
                right = self.source.narrow(node.test, function.module, env, False)
                if condition is not False:
                    flow.extend(
                        self.block(
                            node.body, function, left, stack, reraised, states=states
                        )
                    )
                if condition is not True:
                    flow.extend(
                        self.block(
                            node.orelse, function, right, stack, reraised, states=states
                        )
                    )
                continuing = []
                if condition is not False and "$terminated" not in left:
                    continuing.append(left)
                if condition is not True and "$terminated" not in right:
                    continuing.append(right)
                self.source.merge_environments(env, continuing)
                if not continuing:
                    env["$terminated"] = _Value("terminated")
                    break
                continue
            if isinstance(node, ast.For | ast.AsyncFor | ast.While):
                # Iterations may run zero or more times, like independent branches.
                local = env.copy()
                if isinstance(node, ast.While):
                    flow.extend(self.expression(node.test, function, env, stack))
                else:
                    flow.extend(
                        self.expression(node.iter, function, env, stack, iterated=True)
                    )
                    self.source.assign(
                        node.target,
                        self.source.element(
                            self.source.infer(node.iter, function.module, env)
                        ),
                        local,
                    )
                flow.extend(
                    self.block(
                        node.body, function, local, stack, reraised, states=states
                    )
                )
                flow.extend(
                    self.block(
                        node.orelse,
                        function,
                        env.copy(),
                        stack,
                        reraised,
                        states=states,
                    )
                )
                self._invalidate(node, env)
                continue
            if isinstance(node, ast.Assign | ast.AnnAssign):
                if isinstance(node, ast.AnnAssign) and node.value is None:
                    continue
                if node.value is not None:
                    flow.extend(self.expression(node.value, function, env, stack))
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                inferred = (
                    self.source.infer(node.value, function.module, env)
                    if node.value is not None
                    else self.source.typed(node.annotation, function.module)
                    if isinstance(node, ast.AnnAssign)
                    else _Value()
                )
                for target in targets:
                    self.source.assign(target, inferred, env)
                continue
            if isinstance(node, ast.Return):
                if node.value:
                    flow.extend(self.expression(node.value, function, env, stack))
                env["$terminated"] = _Value("return")
                break
            if isinstance(node, ast.AugAssign):
                flow.extend(self.expression(node.target, function, env, stack))
                flow.extend(self.expression(node.value, function, env, stack))
                target_value = self.source.infer(node.target, function.module, env)
                if all(
                    variant.name
                    in {
                        "builtins.int",
                        "builtins.float",
                        "builtins.str",
                        "builtins.bytes",
                    }
                    for variant in target_value.variants
                ):
                    value = _join(
                        *(
                            replace(variant, literal=None)
                            for variant in target_value.variants
                        )
                    )
                    self.source.assign(node.target, value, env)
                else:
                    flow.unknowns.append(
                        _Unknown(
                            "unresolved augmented assignment protocol",
                            function.name,
                            node.lineno,
                        )
                    )
                    self.source.assign(node.target, _Value(), env)
                continue
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = _Value()
                    elif isinstance(target, ast.Subscript):
                        flow.extend(self.expression(target.value, function, env, stack))
                        flow.extend(self.expression(target.slice, function, env, stack))
                        value = self.source.infer(target.value, function.module, env)
                        if not all(
                            variant.name in {"builtins.dict", "builtins.list"}
                            for variant in value.variants
                        ):
                            flow.unknowns.append(
                                _Unknown(
                                    "unresolved deletion protocol",
                                    function.name,
                                    node.lineno,
                                )
                            )
                    else:
                        flow.unknowns.append(
                            _Unknown(
                                "unresolved deletion protocol",
                                function.name,
                                node.lineno,
                            )
                        )
                continue
            if isinstance(node, ast.Expr):
                if isinstance(node.value, ast.Yield) and function.name in self._yields:
                    if node.value.value:
                        flow.extend(
                            self.expression(node.value.value, function, env, stack)
                        )
                    flow.extend(self._yields[function.name])
                    continue
                flow.extend(self.expression(node.value, function, env, stack))
                continue
            if isinstance(node, ast.Pass | ast.Break | ast.Continue):
                continue
            # Match, try*, etc. need more control-flow knowledge.
            flow.unknowns.append(
                _Unknown(
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

    def _invalidate(self, node: ast.AST, env: _Environment) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Name | ast.Attribute) and isinstance(
                child.ctx, ast.Store
            ):
                env[self.source.key(child)] = _Value()


_ROUTE_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route"}
)
_TRANSPORT_METHODS = frozenset({"sse", "webrtc"})
_RPC_METHOD = "method"


def _operation_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Attribute):
        return decorator.attr == _RPC_METHOD
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {*_ROUTE_METHODS, *_TRANSPORT_METHODS, _RPC_METHOD}
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
