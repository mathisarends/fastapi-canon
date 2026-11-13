from collections.abc import Sequence
from dataclasses import replace
from itertools import chain
from typing import Any

from fastapi import FastAPI

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.error.catalog import registry_scope
from fastapi_canon.error.contracts import INSTALLED_REGISTRY_STATE_KEY
from fastapi_canon.integrations.websockets.channel import request_name
from fastapi_canon.integrations.websockets.contract import contract_from_endpoints
from fastapi_canon.integrations.websockets.definition import (
    ENDPOINT_ATTRIBUTE,
    RpcAnyEndpoint,
    RpcEndpoint,
    RpcSocketEndpoint,
    RpcStreamEndpoint,
)
from fastapi_canon.integrations.websockets.errors import ProtocolDefinitionError
from fastapi_canon.integrations.websockets.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
    RpcSocketDefinition,
    RpcStreamDefinition,
    RpcSubscriptionDefinition,
)


def export_rpc_contract(
    app: FastAPI,
    *,
    base_url: str = "{baseUrl}",
    registered_routes: Sequence[tuple[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Export only mounted RPC endpoints, using their effective registered paths.

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
    for path, route in routes:
        endpoint = getattr(getattr(route, "endpoint", None), ENDPOINT_ATTRIBUTE, None)
        if endpoint is None:
            continue
        if endpoint.name in paths:
            raise ProtocolDefinitionError(
                f"Ambiguous RPC endpoint {endpoint.name!r}: "
                f"{paths[endpoint.name]!r} and {path!r}; use unique endpoint names"
            )
        paths[endpoint.name] = path
        versions.add(endpoint.service.version)
        endpoints.append(replace(endpoint, path=path))
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
    )
    with registry_scope(getattr(app.state, INSTALLED_REGISTRY_STATE_KEY, None)):
        # RpcContract.to_openrpc installs its own registry context; render directly
        # here to preserve the application's catalog for canonical error schemas.
        from fastapi_canon.integrations.websockets.schema import render_openrpc

        document = render_openrpc(
            contract.protocol,
            title=contract.title,
            description=contract.description,
            servers=contract.servers,
            binary_streams=contract.binary_streams,
            sockets=contract.sockets,
        )
    document["x-canon-application"] = True
    return document
