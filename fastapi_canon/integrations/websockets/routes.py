"""Composition checks and OpenRPC export for registered RPC endpoints."""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from dishka import DependencyKey, Scope
from fastapi import FastAPI

from fastapi_canon.diagnostics import CheckIssue, CheckResult, qualified_name
from fastapi_canon.integration import IntegrationRoute, RouteContext

__all__ = ["OPENRPC", "RpcRoute"]

type DependencyRoots = Iterable[tuple[str, Sequence[Any] | None]]


class OpenRpcExporter:
    """Export every registered RPC endpoint into one OpenRPC document."""

    kind = "openrpc"

    def export(
        self, app: FastAPI, routes: Sequence[tuple[str, Any]], *, base_url: str
    ) -> dict[str, Any] | None:
        from fastapi_canon.integrations.websockets.schema.application import (
            export_rpc_contract,
        )

        return export_rpc_contract(app, base_url=base_url, registered_routes=routes)


OPENRPC = OpenRpcExporter()


class RpcRoute(IntegrationRoute):
    """A registered RPC endpoint whose methods inject Dishka services."""

    __slots__ = ("_roots",)

    def __init__(self, roots: Callable[[], DependencyRoots]) -> None:
        self._roots = roots

    @property
    def contract(self) -> OpenRpcExporter:
        return OPENRPC

    def check(self, context: RouteContext) -> None:
        """Require a REQUEST-reachable binding for every injected method service."""
        prefix = f"rpc.method_dependencies:{context.feature}:{context.path}"
        try:
            requirements = tuple(self._roots())
        except Exception:
            context.report(
                CheckIssue(
                    "canon.providers.E002",
                    f"RPC dependencies at {context.path!r} could not be inspected",
                    "Check the mounted channel declarations and handler signatures.",
                    feature_names=(context.feature,),
                    locations=(f"WEBSOCKET {context.path}",),
                )
            )
            context.record(CheckResult(prefix, "incomplete"))
            return
        for method, dependencies in requirements:
            name = f"{prefix}:{method}"
            if dependencies is None or (dependencies and context.external_container):
                context.record(CheckResult(name, "skipped"))
                continue
            missing = [
                dependency
                for dependency in dependencies
                if not context.available(DependencyKey(dependency, ""), Scope.REQUEST)
            ]
            for dependency in missing:
                qualified = qualified_name(dependency)
                context.report(
                    CheckIssue(
                        "canon.providers.E002",
                        f"RPC method {method!r} injects {qualified} without an "
                        "APP/SESSION/REQUEST binding",
                        "Register the default-component service in a feature "
                        "provider, or configure an explicit resolver that owns it.",
                        feature_names=(context.feature,),
                        locations=(f"WEBSOCKET {context.path} ({method})", qualified),
                    )
                )
            context.record(CheckResult(name, "failed" if missing else "passed"))
