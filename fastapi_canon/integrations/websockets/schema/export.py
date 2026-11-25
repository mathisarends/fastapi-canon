import importlib
from collections.abc import Mapping
from dataclasses import replace

from fastapi import FastAPI

from fastapi_canon.integrations.websockets.channel import RpcChannel
from fastapi_canon.integrations.websockets.contract import RpcContract, ServerVariable
from fastapi_canon.integrations.websockets.definition import RpcDefinition
from fastapi_canon.integrations.websockets.protocol import RpcProtocol
from fastapi_canon.integrations.websockets.schema.openrpc import Server


class ProtocolReferenceError(Exception):
    """Raised when a ``module:attribute`` reference names no RPC source."""


# Public protocol loader for external contract-export tooling.
def load_protocol(  # smelt: ignore[VIS009] -- external API
    reference: str,
) -> RpcProtocol:
    """Import a channel or contract and return its internal protocol."""
    source = load_contract_source(reference)
    return source.protocol


def load_contract_source(
    reference: str,
    *,
    base_url: str | None = None,
    variables: Mapping[str, ServerVariable] | None = None,
) -> RpcContract:
    """Import a FastAPI application or contract named by ``module:attribute``.

    An application exports its mounted RPC endpoints; ``base_url`` and server
    ``variables`` set their public origin.
    """
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ProtocolReferenceError(
            f"Expected a module:attribute reference, got {reference!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise ProtocolReferenceError(f"Cannot import {module_name}: {error}") from error
    try:
        source = getattr(module, attribute_name)
    except AttributeError as error:
        raise ProtocolReferenceError(
            f"{module_name} has no attribute {attribute_name}"
        ) from error
    if isinstance(source, FastAPI):
        from fastapi_canon.integrations.websockets.schema.application import (
            application_rpc_contract,
        )

        contract = application_rpc_contract(
            source,
            base_url="{baseUrl}" if base_url is None else base_url,
            variables=variables,
        )
        if contract is None:
            raise ProtocolReferenceError(f"{reference} mounts no RPC endpoints")
        return contract
    if base_url is not None or variables is not None:
        raise ProtocolReferenceError(
            "base_url and variables apply only to FastAPI application sources"
        )
    if isinstance(source, RpcDefinition):
        raise ProtocolReferenceError(
            f"{reference} is an RpcDefinition; reference the FastAPI application "
            "that mounts it instead"
        )
    if isinstance(source, RpcChannel):
        raise ProtocolReferenceError(
            f"{reference} is an RpcChannel; mount it with router.include_channel() "
            "and reference the FastAPI application instead"
        )
    if not isinstance(source, RpcContract):
        raise ProtocolReferenceError(
            f"{reference} is a {type(source).__name__}, not an RpcContract"
        )
    return source


def render_contract(
    source: RpcContract,
    *,
    title: str | None = None,
    description: str | None = None,
    servers: tuple[Server, ...] | None = None,
) -> str:
    """Render a contract as the JSON text committed to the repository."""
    resolved_title = source.title if title is None else title
    resolved_description = source.description if description is None else description
    resolved_servers = tuple(source.servers) if servers is None else servers
    return replace(
        source,
        title=resolved_title,
        description=resolved_description,
        servers=resolved_servers,
    ).to_json()
