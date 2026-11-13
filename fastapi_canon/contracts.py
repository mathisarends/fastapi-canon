"""Export the contracts of a composed application without starting its runtime."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from fastapi_canon.contract_routes import (
    RPC_ENDPOINT_ATTRIBUTE,
    WEBRTC_DECLARATION_ATTRIBUTE,
    iter_contract_routes,
)
from fastapi_canon.feature import Feature, installed_contract_routes
from fastapi_canon.openapi import export_openapi


class ContractKind(StrEnum):
    OPENAPI = auto()
    OPENRPC = auto()
    WEBRTC = auto()


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
    """An application contract snapshot; absent or unselected transports are None."""

    openapi: dict[str, Any] | None = None
    openrpc: dict[str, Any] | None = None
    webrtc: dict[str, Any] | None = None

    @property
    def documents(self) -> dict[str, dict[str, Any]]:
        """Documents keyed by their stable export filenames without extensions."""
        return {
            kind: document
            for kind in ("openapi", "openrpc", "webrtc")
            if (document := getattr(self, kind)) is not None
        }


def export_contracts(
    app: FastAPI,
    *,
    kinds: Sequence[ContractKind] = tuple(ContractKind),
    selection: ContractSelection | None = None,
    base_url: str = "{baseUrl}",
) -> Contracts:
    """Export registered HTTP/SSE, RPC and WebRTC declarations.

    This never enters the lifespan or resolves dependencies. The application
    must already be composed. Its factory and module imports still execute
    normally; keep runtime settings and resource acquisition out of those steps.
    ``base_url`` supplies the public RPC origin, independently of mounted paths.
    Its default is a client-supplied ``baseUrl`` template.
    Feature selections use installed object identity, never feature names.
    """
    if not isinstance(app, FastAPI):
        raise TypeError("export_contracts requires a FastAPI application")
    selected = tuple(kinds)
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(not isinstance(kind, ContractKind) for kind in selected)
    ):
        raise ValueError(
            "kinds must select unique ContractKind members (openapi, openrpc or webrtc)"
        )
    if selection is not None and not isinstance(selection, ContractSelection):
        raise TypeError("selection must be a ContractSelection")
    routes = _select_routes(app, selected, selection) if selection is not None else None
    documents: dict[str, Any] = {}
    endpoints = tuple(
        getattr(route, "endpoint", None)
        for _, route in (iter_contract_routes(app.routes) if routes is None else routes)
    )
    if ContractKind.OPENAPI in selected:
        document = export_openapi(app)
        if routes is not None:
            _select_openapi(document, routes)
        documents["openapi"] = document
    if ContractKind.OPENRPC in selected and any(
        hasattr(endpoint, RPC_ENDPOINT_ATTRIBUTE) for endpoint in endpoints
    ):
        from fastapi_canon.integrations.websockets.schema.application import (
            export_rpc_contract,
        )

        documents["openrpc"] = export_rpc_contract(
            app, base_url=base_url, registered_routes=routes
        )
    if ContractKind.WEBRTC in selected and any(
        hasattr(endpoint, WEBRTC_DECLARATION_ATTRIBUTE) for endpoint in endpoints
    ):
        from fastapi_canon.integrations.webrtc.contract import build_contract

        document = build_contract(app, registered_routes=routes)
        if document["sessions"]:
            documents["webrtc"] = document
    return Contracts(**documents)


def _select_routes(
    app: FastAPI, kinds: tuple[ContractKind, ...], selection: ContractSelection
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
        endpoint = getattr(route, "endpoint", None)
        if (
            (ContractKind.OPENAPI in kinds and _route_key(path, route) in root_http)
            or (
                ContractKind.OPENRPC in kinds
                and hasattr(endpoint, RPC_ENDPOINT_ATTRIBUTE)
            )
            or (
                ContractKind.WEBRTC in kinds
                and hasattr(endpoint, WEBRTC_DECLARATION_ATTRIBUTE)
            )
        ):
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
