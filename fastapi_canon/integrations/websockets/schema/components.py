import re
from collections import Counter
from collections.abc import Iterable
from enum import Enum
from hashlib import sha256
from itertools import chain
from types import UnionType
from typing import Any, Literal, NamedTuple, TypeAliasType, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, create_model
from pydantic.json_schema import JsonSchemaMode

from fastapi_canon.integrations.websockets.errors import ProtocolDefinitionError
from fastapi_canon.integrations.websockets.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
    RpcSubscriptionDefinition,
)

EMPTY_PARAMS_SCHEMA: dict[str, Any] = {
    "additionalProperties": False,
    "type": "object",
}
"""What a method without params accepts: an omitted or empty ``params`` member."""


type SchemaKey = str | tuple[Literal["method", "stream"], str]


class SchemaComponents(NamedTuple):
    schemas: dict[str, Any]
    params: dict[str, str]
    outputs: dict[SchemaKey, dict[str, Any]]
    requests: dict[str, str]


def components(protocol: RpcProtocol) -> SchemaComponents:
    """Build the JSON Schema components referenced by the OpenRPC document."""
    annotations = _output_annotations(protocol)
    adapters: list[_SchemaInput] = [
        _SchemaInput(name, "serialization", TypeAdapter(annotation))
        for name, annotation in annotations.items()
    ]
    params_models = _params_models(protocol, reserved_names=set(annotations))
    for model in params_models.values():
        _add(annotations, model)
    _assert_unique_wire_names(annotations.values())
    adapters.extend(
        _SchemaInput(type_name(model), "validation", TypeAdapter(model))
        for model in dict.fromkeys(params_models.values())
    )
    adapters.extend(
        _SchemaInput(
            ("method", method.name), "serialization", TypeAdapter(method.result)
        )
        for method in protocol.methods
    )
    adapters.extend(
        _SchemaInput(
            ("stream", stream.name), "serialization", TypeAdapter(stream.payload)
        )
        for stream in protocol.subscriptions
    )
    for socket in protocol.sockets:
        if socket.message_contract is not None:
            adapters.extend(
                [
                    _SchemaInput(
                        socket_message_schema_name(socket.name, "client"),
                        "validation",
                        socket.message_contract.client,
                    ),
                    _SchemaInput(
                        socket_message_schema_name(socket.name, "server"),
                        "serialization",
                        socket.message_contract.server,
                    ),
                ]
            )
    roots, root = TypeAdapter.json_schemas(adapters, by_alias=True)
    definitions: dict[str, Any] = root.get("$defs", {})
    params_names = {
        operation: roots[(type_name(model), "validation")]["$ref"].removeprefix(
            "#/$defs/"
        )
        for operation, model in params_models.items()
    }
    requests = _request_names(protocol, definitions)
    for method in protocol.methods:
        name = requests[method.name]
        definitions[name] = _request_schema(
            method, definitions, name=name, params_name=params_names.get(method.name)
        )
    outputs = {
        key: schema for (key, mode), schema in roots.items() if mode == "serialization"
    }
    for notification in protocol.notifications:
        name = notification_schema_name(notification.name)
        definitions[name] = _notification_schema(
            notification, name, outputs[type_name(notification.payload)]
        )
    for socket in protocol.sockets:
        if socket.message_contract is not None:
            directions: tuple[tuple[str, JsonSchemaMode], ...] = (
                ("client", "validation"),
                ("server", "serialization"),
            )
            for direction, mode in directions:
                name = socket_message_schema_name(socket.name, direction)
                if name in definitions:
                    raise ProtocolDefinitionError(
                        f"Duplicate socket schema name: {name}"
                    )
                definitions[name] = roots[(name, mode)]
    return SchemaComponents(definitions, params_names, outputs, requests)


def socket_message_schema_name(name: str, direction: str) -> str:
    parts = (part for part in re.split(r"[^a-zA-Z0-9]+", name) if part)
    return "".join(part.capitalize() for part in parts) + direction.title() + "Message"


def described(entry: dict[str, Any], summary: str | None) -> dict[str, Any]:
    """Attach a summary only when the definition carries one."""
    if summary is not None:
        entry["summary"] = summary
    return entry


def type_name(annotation: Any) -> str:
    name = getattr(annotation, "__name__", None)
    if not isinstance(name, str):
        raise ProtocolDefinitionError(
            f"Protocol type has no stable schema name: {annotation!r}"
        )
    return name


def notification_schema_name(name: str) -> str:
    parts = (part for part in re.split(r"[^a-zA-Z0-9]+", name) if part)
    return "".join(part.capitalize() for part in parts) + "Notification"


def _output_annotations(protocol: RpcProtocol) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    for method in protocol.methods:
        _add_result_types(annotations, method.result)
    for notification in protocol.notifications:
        _add(annotations, notification.payload)
    for subscription in protocol.subscriptions:
        _add_result_types(annotations, subscription.payload)
    for notification_type in protocol.notification_types:
        _add(annotations, notification_type.payload)
    for operation in chain[
        RpcMethodDefinition | RpcNotificationDefinition | RpcSubscriptionDefinition
    ](protocol.methods, protocol.notifications, protocol.subscriptions):
        for error in operation.raises:
            if error.details_type is not None:
                _add(annotations, error.details_type)
    return annotations


def _params_models(
    protocol: RpcProtocol, *, reserved_names: set[str]
) -> dict[str, type[BaseModel]]:
    params: dict[str, type[BaseModel]] = {}
    generated: dict[str, type[BaseModel]] = {}
    for operation in chain[RpcMethodDefinition | RpcSubscriptionDefinition](
        protocol.methods, protocol.subscriptions
    ):
        if operation.params is None:
            continue
        params[operation.name] = operation.params
        if operation.params_fields:
            generated[operation.name] = operation.params
        else:
            reserved_names.add(type_name(operation.params))

    counts = Counter(type_name(model) for model in generated.values())
    used = reserved_names.union(counts)
    for operation_name, model in sorted(generated.items()):
        name = type_name(model)
        if counts[name] == 1 and name not in reserved_names:
            continue
        # A channel can reuse the same params class under multiple wire names.
        # Use separate schema models without modifying that runtime class.
        candidate = _distinct_schema_name(name, operation_name, used)
        params[operation_name] = create_model(candidate, __base__=model)
        used.add(candidate)
    return params


def _add_result_types(annotations: dict[str, Any], annotation: Any) -> None:
    if isinstance(annotation, TypeAliasType) or (
        isinstance(annotation, type) and issubclass(annotation, (BaseModel, Enum))
    ):
        _add(annotations, annotation)
        return
    for argument in get_args(annotation):
        _add_result_types(annotations, argument)


def _add(annotations: dict[str, Any], annotation: Any) -> None:
    name = type_name(annotation)
    existing = annotations.get(name)
    if existing is not None and existing != annotation:
        raise ProtocolDefinitionError(f"Duplicate protocol schema name: {name}")
    annotations[name] = annotation


def _request_names(
    protocol: RpcProtocol, definitions: dict[str, Any]
) -> dict[str, str]:
    reserved = set(definitions)
    reserved.update(
        notification_schema_name(item.name) for item in protocol.notifications
    )
    for socket in protocol.sockets:
        if socket.message_contract is not None:
            reserved.update(
                socket_message_schema_name(socket.name, direction)
                for direction in ("client", "server")
            )
    preferred = Counter(method.request_name for method in protocol.methods)
    used = reserved.union(preferred)
    names: dict[str, str] = {}
    for method in sorted(protocol.methods, key=lambda item: item.name):
        name = method.request_name
        if name in reserved or preferred[name] > 1:
            name = _distinct_schema_name(name, method.name, used)
            used.add(name)
        names[method.name] = name
    return names


def _distinct_schema_name(name: str, operation: str, used: set[str]) -> str:
    digest = sha256(operation.encode()).hexdigest()[:12]
    candidate = f"{name}For{digest}"
    while candidate in used:
        candidate += "X"
    return candidate


def _request_schema(
    method: RpcMethodDefinition,
    definitions: dict[str, Any],
    *,
    name: str,
    params_name: str | None,
) -> dict[str, Any]:
    required = ["jsonrpc", "method"]
    if params_name is None:
        params = EMPTY_PARAMS_SCHEMA
    else:
        params = _ref(params_name)
        if definitions[params_name].get("required"):
            required.append("params")
    schema: dict[str, Any] = {
        "additionalProperties": False,
        "type": "object",
        "title": name,
        "properties": {
            "jsonrpc": {"const": "2.0", "type": "string"},
            "id": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                    {"type": "null"},
                ],
                "default": None,
            },
            "method": {"const": method.name, "type": "string"},
            "params": params,
            "meta": {
                "type": "object",
                "additionalProperties": {"type": "string", "maxLength": 4096},
                "propertyNames": {"enum": ["traceparent", "tracestate", "baggage"]},
                "maxProperties": 3,
                "x-rpc-max-bytes": 4096,
            },
        },
        "required": required,
    }
    if method.summary is not None:
        schema["description"] = method.summary
    return schema


def _notification_schema(
    notification: RpcNotificationDefinition,
    schema_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "additionalProperties": False,
        "type": "object",
        "title": schema_name,
        "properties": {
            "jsonrpc": {"const": "2.0", "type": "string"},
            "method": {"const": notification.name, "type": "string"},
            "params": payload,
        },
        "required": ["jsonrpc", "method", "params"],
    }
    if notification.summary is not None:
        schema["description"] = notification.summary
    return schema


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _assert_unique_wire_names(annotations: Iterable[Any]) -> None:
    seen_models: set[type[BaseModel]] = set()

    def visit(annotation: Any) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in seen_models:
                return
            seen_models.add(annotation)
            wire_fields: dict[str, str] = {}
            for python_name, model_field in annotation.model_fields.items():
                wire_name = (
                    model_field.serialization_alias or model_field.alias or python_name
                )
                previous = wire_fields.get(wire_name)
                if previous is not None:
                    raise ProtocolDefinitionError(
                        f"RPC model {annotation.__name__} maps both "
                        f"{previous!r} and {python_name!r} to wire field "
                        f"{wire_name!r}"
                    )
                wire_fields[wire_name] = python_name
                visit(model_field.annotation)
            return
        origin = get_origin(annotation)
        if origin is not None or isinstance(annotation, UnionType):
            for argument in get_args(annotation):
                visit(argument)

    for annotation in annotations:
        visit(annotation)


class _SchemaInput(NamedTuple):
    key: SchemaKey
    mode: JsonSchemaMode
    adapter: TypeAdapter[Any]
