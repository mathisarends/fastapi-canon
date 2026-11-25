import re
from collections import Counter
from enum import Enum
from hashlib import sha256
from itertools import chain
from typing import Any, Literal, NamedTuple, TypeAliasType, get_args

from pydantic import AliasChoices, BaseModel, TypeAdapter, create_model
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue
from pydantic_core import core_schema

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


type EnvelopeKey = tuple[Literal["method", "notification", "client", "server"], str]
type SchemaKey = str | EnvelopeKey | tuple[Literal["stream"], str]


_SOCKET_MODES: tuple[tuple[Literal["client", "server"], JsonSchemaMode], ...] = (
    ("client", "validation"),
    ("server", "serialization"),
)


class SchemaComponents(NamedTuple):
    schemas: dict[str, Any]
    params: dict[str, str]
    outputs: dict[SchemaKey, dict[str, Any]]
    envelopes: dict[EnvelopeKey, str]


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
    adapters.extend(
        _SchemaInput(
            ("notification", event.name), "serialization", TypeAdapter(event.payload)
        )
        for event in protocol.notifications
    )
    for socket in protocol.sockets:
        if socket.message_contract is not None:
            adapters.extend(
                [
                    _SchemaInput(
                        ("client", socket.name),
                        "validation",
                        socket.message_contract.client,
                    ),
                    _SchemaInput(
                        ("server", socket.name),
                        "serialization",
                        socket.message_contract.server,
                    ),
                ]
            )
    roots, root = TypeAdapter.json_schemas(adapters, schema_generator=_RpcJsonSchema)
    definitions: dict[str, Any] = root.get("$defs", {})
    params_names = {
        operation: roots[(type_name(model), "validation")]["$ref"].removeprefix(
            "#/$defs/"
        )
        for operation, model in params_models.items()
    }
    envelopes = _envelope_names(protocol, definitions)
    for method in protocol.methods:
        name = envelopes[("method", method.name)]
        definitions[name] = _request_schema(
            method, definitions, name=name, params_name=params_names.get(method.name)
        )
    outputs = {
        key: schema for (key, mode), schema in roots.items() if mode == "serialization"
    }
    for notification in protocol.notifications:
        name = envelopes[("notification", notification.name)]
        definitions[name] = _notification_schema(
            notification, name, outputs[("notification", notification.name)]
        )
    for socket in protocol.sockets:
        if socket.message_contract is not None:
            for direction, mode in _SOCKET_MODES:
                key = (direction, socket.name)
                definitions[envelopes[key]] = roots[(key, mode)]
    return SchemaComponents(definitions, params_names, outputs, envelopes)


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


def _output_annotations(protocol: RpcProtocol) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    for method in protocol.methods:
        _add_result_types(annotations, method.result)
    for notification in protocol.notifications:
        _add_result_types(annotations, notification.payload)
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


def _envelope_names(
    protocol: RpcProtocol, definitions: dict[str, Any]
) -> dict[EnvelopeKey, str]:
    preferred: dict[EnvelopeKey, str] = {
        ("method", item.name): item.request_name for item in protocol.methods
    }
    for event in protocol.notifications:
        preferred[("notification", event.name)] = (
            _pascal_name(event.name) + "Notification"
        )
    for socket in protocol.sockets:
        if socket.message_contract is not None:
            for direction, _ in _SOCKET_MODES:
                preferred[(direction, socket.name)] = (
                    _pascal_name(socket.name) + direction.title() + "Message"
                )
    counts = Counter(preferred.values())
    used = set(definitions).union(counts)
    for key, name in sorted(preferred.items()):
        if name in definitions or counts[name] > 1:
            preferred[key] = _distinct_schema_name(name, key[1], used)
            used.add(preferred[key])
    return preferred


def _pascal_name(name: str) -> str:
    parts = (part for part in re.split(r"[^a-zA-Z0-9]+", name) if part)
    return "".join(part.capitalize() for part in parts)


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
        if method.params_default is None and definitions[params_name].get("required"):
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


class _SchemaInput(NamedTuple):
    key: SchemaKey
    mode: JsonSchemaMode
    adapter: TypeAdapter[Any]


class _RpcJsonSchema(GenerateJsonSchema):
    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        # Inputs follow each model's accepted keys. RPC outputs always use aliases.
        previous = self.by_alias
        self.by_alias = self.mode != "validation" or schema["cls"].model_config.get(
            "validate_by_alias", True
        )
        try:
            _check_wire_fields(schema["cls"], self.mode, by_alias=self.by_alias)
            return super().model_schema(schema)
        finally:
            self.by_alias = previous


def _check_wire_fields(
    model: type[BaseModel], mode: JsonSchemaMode, *, by_alias: bool
) -> None:
    names: dict[str, str] = {}
    for name, field in model.model_fields.items():
        if mode == "serialization" and field.exclude:
            continue
        wire_name = name
        if mode == "serialization":
            wire_name = field.serialization_alias or name
        elif by_alias and field.validation_alias is not None:
            alias = field.validation_alias
            if isinstance(alias, str):
                wire_name = alias
            else:
                paths = (
                    alias.convert_to_aliases()
                    if isinstance(alias, AliasChoices)
                    else [alias.path]
                )
                flat = next(
                    (
                        path[0]
                        for path in paths
                        if len(path) == 1 and isinstance(path[0], str)
                    ),
                    None,
                )
                if flat is not None:
                    wire_name = flat
                elif not model.model_config.get("validate_by_name"):
                    raise ProtocolDefinitionError(
                        f"RPC model {model.__name__}.{name} needs a flat validation "
                        "alias or validate_by_name=True for named RPC params"
                    )
        previous = names.setdefault(wire_name, name)
        if previous != name:
            raise ProtocolDefinitionError(
                f"RPC model {model.__name__} maps both {previous!r} and {name!r} "
                f"to wire field {wire_name!r}"
            )
