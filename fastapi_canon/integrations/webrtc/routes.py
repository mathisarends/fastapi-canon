"""Composition checks and contract export for registered WebRTC sessions."""

from collections.abc import Sequence
from typing import Any

from dishka import DependencyKey, Scope
from fastapi import FastAPI

from fastapi_canon.diagnostics import CheckIssue, CheckResult, qualified_name
from fastapi_canon.integration import IntegrationRoute, RouteContext

__all__ = ["WEBRTC", "WebRtcRoute"]


class WebRtcExporter:
    """Export every registered WebRTC session into one ``webrtc`` document."""

    kind = "webrtc"

    def export(
        self, app: FastAPI, routes: Sequence[tuple[str, Any]], *, base_url: str
    ) -> dict[str, Any] | None:
        from fastapi_canon.integrations.webrtc.contract import build_contract

        document = build_contract(app, registered_routes=routes)
        return document if document["sessions"] else None


WEBRTC = WebRtcExporter()


class WebRtcRoute(IntegrationRoute):
    """A registered WebRTC session whose factory injects Dishka services."""

    __slots__ = ("_roots",)

    def __init__(self, roots: tuple[DependencyKey, ...] | None) -> None:
        self._roots = roots

    @property
    def contract(self) -> WebRtcExporter:
        return WEBRTC

    def check(self, context: RouteContext) -> None:
        """Require an APP or SESSION binding for every injected factory service."""
        name = f"webrtc.factory_dependencies:{context.feature}:{context.path}"
        roots = self._roots
        if roots is None or (roots and context.external_container):
            context.record(CheckResult(name, "skipped"))
            return
        missing = [key for key in roots if not context.available(key, Scope.SESSION)]
        for key in missing:
            qualified = qualified_name(key.type_hint)
            context.report(
                CheckIssue(
                    "canon.providers.E004",
                    f"WebRTC factory {context.path!r} injects {qualified} without an "
                    f"APP/SESSION binding (component {key.component!r})",
                    "Register the factory service at APP or SESSION scope; use a "
                    "child REQUEST scope for per-message work.",
                    feature_names=(context.feature,),
                    locations=(f"WEBSOCKET {context.path}", qualified),
                )
            )
        context.record(CheckResult(name, "failed" if missing else "passed"))
