"""Extension points for optional integrations.

An application enables an integration once in ``Composition(integrations=...)``.
Features give it parts through ``Feature(integrations=...)``. Transports attach an
``IntegrationRoute`` to their endpoints, so composition checks and contract export
include them without the core knowing the transport.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from fastapi_canon.diagnostics import CheckIssue, CheckResult
from fastapi_canon.exceptions import FeatureConfigurationError

if TYPE_CHECKING:
    from dishka import AsyncContainer, Provider, Scope
    from dishka.entities.key import DependencyKey
    from fastapi import APIRouter, FastAPI
    from starlette.types import Lifespan

__all__ = [
    "ContractExporter",
    "Installer",
    "Integration",
    "IntegrationContext",
    "IntegrationError",
    "IntegrationPart",
    "IntegrationRoute",
    "RouteContext",
    "attach_route",
    "attached_route",
]

type Parts[PartT] = tuple[tuple[str, PartT], ...]
"""``(feature name, part)`` pairs in composition order."""


class Integration:
    """An application-wide integration, enabled once per composition.

    Subclass it and override only the phases you need. Every feature part whose
    ``integration`` names this class is passed to its phases. A part whose
    integration is not enabled is a composition error.
    """

    __slots__ = ()

    def providers(
        self, parts: Parts[Any], providers: tuple[Provider, ...]
    ) -> Sequence[Provider]:
        """Return Dishka providers added after the feature *providers*."""
        return ()

    def prepare(self, context: IntegrationContext) -> Installer | None:
        """Check the prepared application without side effects; return its installer."""
        return None


class IntegrationPart(Protocol):
    """A feature-owned contribution to one integration."""

    integration: ClassVar[type[Integration]]


@dataclass(frozen=True, slots=True)
class IntegrationContext:
    """The prepared composition, available to ``Integration.prepare``."""

    app: FastAPI
    parts: Parts[Any]
    routers: tuple[APIRouter, ...]
    container: AsyncContainer | None


@dataclass(frozen=True, slots=True)
class Installer:
    """Install a prepared integration and optionally contribute a lifespan.

    ``before_routes`` installers run before feature routers are included, for
    example to add outer middleware. Others run after routes and error handlers.
    """

    install: Callable[[FastAPI, AsyncContainer | None], Lifespan[FastAPI] | None]
    before_routes: bool = False


class IntegrationError(FeatureConfigurationError):
    """A composition failure reported with an integration-owned diagnostic code."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


class ContractExporter(Protocol):
    """Export one contract document from the routes that carry this exporter."""

    @property
    def kind(self) -> str:
        """The stable document name, also used as its ``<kind>.json`` filename."""
        ...

    def export(
        self, app: FastAPI, routes: Sequence[tuple[str, Any]], *, base_url: str
    ) -> dict[str, Any] | None:
        """Return the document for *routes*, or ``None`` when none apply."""
        ...


class IntegrationRoute:
    """Transport metadata attached to one route endpoint with ``attach_route``."""

    __slots__ = ()

    @property
    def contract(self) -> ContractExporter | None:
        """The exporter that documents this route, if any."""
        return None

    def check(self, context: RouteContext) -> None:
        """Check the route against the composed application's dependencies."""
        return None


@dataclass(frozen=True, slots=True)
class RouteContext:
    """One registered integration route during composition checks."""

    feature: str
    path: str
    container: AsyncContainer | None
    external_container: bool
    """Whether an application-owned Dishka container replaces Canon's."""
    available: Callable[[DependencyKey, Scope], bool]
    """Whether a dependency is bound at or above the given scope."""
    _issues: list[CheckIssue] = field(repr=False)
    _checks: list[CheckResult] = field(repr=False)

    def report(self, issue: CheckIssue) -> None:
        self._issues.append(issue)

    def record(self, result: CheckResult) -> None:
        self._checks.append(result)


def attach_route(endpoint: Callable[..., Any], route: IntegrationRoute) -> None:
    """Attach integration metadata to a route endpoint before registration."""
    if not isinstance(route, IntegrationRoute):
        raise TypeError("route must be an IntegrationRoute")
    endpoint.__dict__[_ROUTE_ATTRIBUTE] = route


def attached_route(endpoint: object) -> IntegrationRoute | None:
    """Return the integration metadata attached to *endpoint*, if any."""
    route = getattr(endpoint, _ROUTE_ATTRIBUTE, None)
    return route if isinstance(route, IntegrationRoute) else None


_ROUTE_ATTRIBUTE = "__canon_integration_route__"
