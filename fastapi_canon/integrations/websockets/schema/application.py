from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import chain
from typing import Any

from fastapi import FastAPI
from starlette.routing import compile_path

from fastapi_canon.error import installed_catalog
from fastapi_canon.errors import ConfigurationError
from fastapi_canon.integrations.websockets.channel import request_name
from fastapi_canon.integrations.websockets.contract import (
    RpcContract,
    ServerVariable,
    contract_from_endpoints,
)
from fastapi_canon.integrations.websockets.definition import (
    DEFAULT_ENDPOINT_NAME_ATTRIBUTE,
    ENDPOINT_ATTRIBUTE,
    RpcAnyEndpoint,
    RpcEndpoint,
    RpcSocketEndpoint,
    RpcStreamEndpoint,
    channel_name_for_path,
)
from fastapi_canon.integrations.websockets.errors import ProtocolDefinitionError
from fastapi_canon.integrations.websockets.protocol import (
    ChannelPathParams,
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
    RpcSocketDefinition,
    RpcStreamDefinition,
    RpcSubscriptionDefinition,
)
from fastapi_canon.routing.traversal import iter_contract_routes


def export_rpc_contract(
    app: FastAPI,
    *,
    base_url: str = "{baseUrl}",
    registered_routes: Sequence[tuple[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Export the OpenRPC document of the application's mounted RPC endpoints."""
    contract = application_rpc_contract(
        app, base_url=base_url, registered_routes=registered_routes
    )
    if contract is None:
        return None
    document = contract.to_openrpc()
    document["x-canon-application"] = True
    return document


def application_rpc_contract(
    app: FastAPI,
    *,
    base_url: str = "{baseUrl}",
    variables: Mapping[str, ServerVariable] | None = None,
    registered_routes: Sequence[tuple[str, Any]] | None = None,
) -> RpcContract | None:
    """Collect only mounted RPC endpoints, using their effective registered paths.

    Endpoint and operation names must be unique within the exported selection.
    Mounting one endpoint twice is ambiguous for the current one-server-per-method
    client contract and is rejected with both paths rather than selecting one.
    """
    endpoints: list[RpcAnyEndpoint] = []
    paths: dict[str, str] = {}
    versions: set[int] = set()
    routes = (
        iter_contract_routes(app.routes)
        if registered_routes is None
        else registered_routes
    )
    mounted: list[tuple[RpcAnyEndpoint, bool]] = []
    for path, route in routes:
        handler = getattr(route, "endpoint", None)
        endpoint = getattr(handler, ENDPOINT_ATTRIBUTE, None)
        if endpoint is not None:
            default_name = getattr(handler, DEFAULT_ENDPOINT_NAME_ATTRIBUTE, False)
            mounted.append((replace(endpoint, path=path), default_name))

    name_counts = Counter(endpoint.name for endpoint, _ in mounted)
    for endpoint, default_name in mounted:
        path = endpoint.path
        name = endpoint.name
        if default_name and name_counts[name] > 1:
            name = channel_name_for_path(path)
        if name in paths:
            raise ProtocolDefinitionError(
                f"Ambiguous RPC endpoint {name!r}: "
                f"{paths[name]!r} and {path!r}; use unique endpoint names"
            )
        paths[name] = path
        versions.add(endpoint.service.version)
        endpoints.append(
            replace(endpoint, name=name) if name != endpoint.name else endpoint
        )
    if not endpoints:
        return None
    if len(versions) != 1:
        raise ProtocolDefinitionError(
            "Mounted RPC endpoints use different protocol versions"
        )

    methods: list[RpcMethodDefinition] = []
    notifications: list[RpcNotificationDefinition] = []
    subscriptions: list[RpcSubscriptionDefinition] = []
    notification_types: dict[str, Any] = {}
    streams = []
    sockets = []
    for endpoint in sorted(endpoints, key=lambda item: item.name):
        match endpoint:
            case RpcEndpoint():
                _validate_path_parameters(endpoint)
                for channel in endpoint.channels:
                    protocol = channel.freeze()
                    methods.extend(
                        replace(
                            item,
                            server=endpoint.name,
                            request_name=request_name(item.name),
                        )
                        for item in protocol.methods
                    )
                    notifications.extend(
                        replace(item, server=endpoint.name)
                        for item in protocol.notifications
                    )
                    subscriptions.extend(
                        replace(item, server=endpoint.name)
                        for item in protocol.subscriptions
                    )
                    for item in protocol.notification_types:
                        previous = notification_types.get(item.name)
                        if previous is not None and previous != item:
                            raise ProtocolDefinitionError(
                                f"Conflicting notification type {item.name!r}"
                            )
                        notification_types[item.name] = item
            case RpcStreamEndpoint():
                streams.append(replace(endpoint.stream, server=endpoint.name))
            case RpcSocketEndpoint():
                sockets.append(replace(endpoint.socket, server=endpoint.name))
    names: set[str] = set()
    for operation in chain[
        RpcMethodDefinition
        | RpcNotificationDefinition
        | RpcSubscriptionDefinition
        | RpcStreamDefinition
        | RpcSocketDefinition
    ](methods, notifications, subscriptions, streams, sockets):
        if operation.name in names:
            raise ProtocolDefinitionError(
                f"Duplicate mounted RPC operation {operation.name!r}"
            )
        names.add(operation.name)
    for name in names:
        parts = name.split(".")
        if any(".".join(parts[:index]) in names for index in range(1, len(parts))):
            raise ProtocolDefinitionError(
                f"RPC operation {name!r} conflicts with a namespace"
            )
    protocol = RpcProtocol(
        methods=methods,
        notifications=notifications,
        subscriptions=subscriptions,
        notification_types=notification_types.values(),
        streams=streams,
        sockets=sockets,
        version=versions.pop(),
    )
    contract = contract_from_endpoints(
        sorted(endpoints, key=lambda item: item.name),
        protocol,
        title=app.title,
        description=app.description,
        base_url=base_url,
        variables=variables,
    )
    # Canonical error schemas use the application's installed catalog.
    return contract.with_error_catalog(installed_catalog(app))


def _validate_path_parameters(endpoint: RpcEndpoint[Any]) -> None:
    path_model = endpoint.path_model
    if path_model is None or not issubclass(path_model, ChannelPathParams):
        return
    _, _, variables = compile_path(endpoint.path)
    if missing := path_model.missing_path_inputs(variables):
        names = ", ".join(repr(name) for name in sorted(missing))
        raise ConfigurationError(
            f"RPC path parameters {names} are not variables of {endpoint.path!r}"
        )
