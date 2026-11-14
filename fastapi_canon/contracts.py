"""Export the contracts of a composed application without starting its runtime."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.feature import Feature, installed_contract_routes
from fastapi_canon.integration import ContractExporter, attached_route
from fastapi_canon.openapi import export_openapi

OPENAPI = "openapi"
"""The contract kind of the application's HTTP OpenAPI document."""


@dataclass(frozen=True, slots=True)
class ContractSelection:
    """Select installed feature objects and/or exact effective route paths.

    Entries within a field are combined with OR; fields are combined with AND.
    A feature reference selects all of its registered occurrences.
    """

    features: tuple[Feature, ...] | None = None
    paths: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.features is None and self.paths is None:
            raise ValueError("ContractSelection needs features or paths")
        if isinstance(self.features, Feature):
            raise TypeError("features must be a tuple of Feature objects, not one")
        if isinstance(self.paths, str):
            raise TypeError("paths must be a tuple of path templates, not a string")
        if self.features is not None:
            features = tuple(self.features)
            if any(not isinstance(feature, Feature) for feature in features):
                raise TypeError("features must contain Feature objects, not names")
            if not features or len({id(feature) for feature in features}) != len(
                features
            ):
                raise ValueError("features must contain unique Feature objects")
            object.__setattr__(self, "features", features)
        if self.paths is not None:
            paths = tuple(self.paths)
            if any(
                not isinstance(path, str) or not path.startswith("/") for path in paths
            ):
                raise ValueError(
                    "paths must contain absolute registered path templates"
                )
            if not paths or len(set(paths)) != len(paths):
                raise ValueError("paths must contain unique registered path templates")
            object.__setattr__(self, "paths", paths)


@dataclass(frozen=True, slots=True)
class Contracts:
    """An application contract snapshot, keyed by contract kind.

    Kinds are ``openapi`` plus those of the integration routes that the
    application registers, such as ``openrpc`` or ``webrtc``.
    """

    documents: Mapping[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "documents", MappingProxyType(dict(self.documents)))

    @property
    def openapi(self) -> dict[str, Any] | None:
        """The HTTP OpenAPI document, if selected."""
        return self.documents.get(OPENAPI)

    @property
    def openrpc(self) -> dict[str, Any] | None:
        """The WebSocket OpenRPC document, if any RPC endpoint is registered."""
        return self.documents.get("openrpc")


def export_contracts(
    app: FastAPI,
    *,
    kinds: Sequence[str] | None = None,
    selection: ContractSelection | None = None,
    base_url: str = "{baseUrl}",
) -> Contracts:
    """Export OpenAPI and the contracts of registered integration routes.

    ``kinds`` selects documents by kind; ``None`` exports every available kind.

    This never enters the lifespan or resolves dependencies. The application
    must already be composed. Its factory and module imports still execute
    normally; keep runtime settings and resource acquisition out of those steps.
    ``base_url`` supplies the public RPC origin, independently of mounted paths.
    Its default is a client-supplied ``baseUrl`` template.
    Feature selections use installed object identity, never feature names.
    """
    if not isinstance(app, FastAPI):
        raise TypeError("export_contracts requires a FastAPI application")
    selected = None if kinds is None else tuple(kinds)
    if selected is not None and (
        not selected
        or len(set(selected)) != len(selected)
        or any(not isinstance(kind, str) or not kind for kind in selected)
    ):
        raise ValueError("kinds must contain unique, nonempty contract kind names")
    if selection is not None and not isinstance(selection, ContractSelection):
        raise TypeError("selection must be a ContractSelection")
    routes = _select_routes(app, selected, selection) if selection is not None else None
    candidates = tuple(iter_contract_routes(app.routes)) if routes is None else routes
    documents: dict[str, dict[str, Any]] = {}
    if selected is None or OPENAPI in selected:
        document = export_openapi(app)
        if routes is not None:
            _select_openapi(document, routes)
        documents[OPENAPI] = document
    for kind, exporter in route_exporters(candidates).items():
        if selected is not None and kind not in selected:
            continue
        exported = exporter.export(app, candidates, base_url=base_url)
        if exported is not None:
            documents[kind] = exported
    return Contracts(documents)


def route_exporters(
    routes: Sequence[tuple[str, Any]],
) -> dict[str, ContractExporter]:
    """Return the distinct contract exporters of *routes*, keyed by kind."""
    exporters: dict[str, ContractExporter] = {}
    for _, route in routes:
        attached = attached_route(getattr(route, "endpoint", None))
        exporter = attached.contract if attached is not None else None
        if exporter is None:
            continue
        previous = exporters.setdefault(exporter.kind, exporter)
        if previous is not exporter and type(previous) is not type(exporter):
            raise ValueError(
                f"Contract kind {exporter.kind!r} is exported by both "
                f"{type(previous).__name__} and {type(exporter).__name__}"
            )
        if exporter.kind == OPENAPI:
            raise ValueError("Contract kind 'openapi' is reserved for HTTP routes")
    return exporters


def _select_routes(
    app: FastAPI, kinds: tuple[str, ...] | None, selection: ContractSelection
) -> tuple[tuple[str, Any], ...]:
    features, owned = installed_contract_routes(app)
    requested = {id(feature) for feature in selection.features or ()}
    foreign = [
        feature.name
        for feature in selection.features or ()
        if not any(feature is installed for installed in features)
    ]
    if foreign:
        raise ValueError(
            "Feature objects are not installed in this application: "
            + ", ".join(foreign)
            + "; use the exact Feature instances passed to Composition"
        )
    owned_keys = {
        (path, id(route)) for feature, path, route in owned if id(feature) in requested
    }
    root_http = {
        _route_key(path, route)
        for path, route in iter_contract_routes(app.routes, include_mounts=False)
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.include_in_schema
    }
    eligible = []
    for path, route in iter_contract_routes(app.routes):
        attached = attached_route(getattr(route, "endpoint", None))
        exporter = attached.contract if attached is not None else None
        if (
            (kinds is None or OPENAPI in kinds) and _route_key(path, route) in root_http
        ) or (exporter is not None and (kinds is None or exporter.kind in kinds)):
            eligible.append((path, route))
    missing = set(selection.paths or ()) - {path for path, _ in eligible}
    if missing:
        raise ValueError(
            "No registered routes for selected contract kinds at: "
            + ", ".join(sorted(missing))
            + "; mounted HTTP applications keep separate OpenAPI documents"
        )
    chosen = tuple(
        (path, route)
        for path, route in eligible
        if (selection.features is None or _route_key(path, route) in owned_keys)
        and (selection.paths is None or path in selection.paths)
    )
    if not chosen:
        raise ValueError("Contract selection matches no documented registered routes")
    return chosen


def _route_key(path: str, route: Any) -> tuple[str, int]:
    return path, id(getattr(route, "original_route", route))


def _select_openapi(
    document: dict[str, Any], routes: tuple[tuple[str, Any], ...]
) -> None:
    methods: dict[str, set[str]] = {}
    for path, route in routes:
        if isinstance(getattr(route, "original_route", route), APIRoute):
            methods.setdefault(path, set()).update(
                method.lower() for method in route.methods
            )
    http_methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    document["paths"] = {
        path: {
            name: value
            for name, value in item.items()
            if name not in http_methods or name in methods[path]
        }
        for path, item in document.get("paths", {}).items()
        if path in methods and any(method in item for method in methods[path])
    }
    components = document.get("components", {})
    schemas = components.pop("schemas", {})
    reachable: dict[str, Any] = {}
    prefix = "#/components/schemas/"
    pending: list[Any] = [document]
    while pending:
        value = pending.pop()
        match value:
            case dict():
                pending.extend(value.values())
            case list():
                pending.extend(value)
            case str() if value.startswith(prefix):
                name = (
                    value[len(prefix) :]
                    .split("/")[0]
                    .replace("~1", "/")
                    .replace("~0", "~")
                )
                if name in schemas and name not in reachable:
                    reachable[name] = schemas[name]
                    pending.append(schemas[name])
    if reachable:
        components["schemas"] = dict(sorted(reachable.items()))
    elif "components" in document and not components:
        del document["components"]
    used_tags = {
        tag
        for item in document["paths"].values()
        for name, operation in item.items()
        if name in http_methods
        for tag in operation.get("tags", ())
    }
    if "tags" in document:
        tags = [tag for tag in document["tags"] if tag.get("name") in used_tags]
        if tags:
            document["tags"] = tags
        else:
            del document["tags"]
