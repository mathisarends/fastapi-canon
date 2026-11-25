from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ValidationError
from pydantic_core import InitErrorDetails

from fastapi_canon.integrations.websockets.adapter import adapter
from fastapi_canon.integrations.websockets.connection import RpcConnection
from fastapi_canon.integrations.websockets.dependencies import (
    EmptyResolver,
    RpcResolver,
)
from fastapi_canon.integrations.websockets.envelopes import RpcRequestEnvelope
from fastapi_canon.integrations.websockets.errors import (
    ProtocolDefinitionError,
    RpcInvalidParamsError,
    RpcInvalidRequestError,
)
from fastapi_canon.integrations.websockets.protocol import (
    RpcMethodDefinition,
    RpcProtocol,
    RpcSubscriptionDefinition,
)


@dataclass(frozen=True, slots=True)
class RpcInvocation:
    request: RpcRequestEnvelope
    method: RpcMethodDefinition
    params: BaseModel | None


class RpcDispatcher:
    """Resolve, scope, and invoke the free functions in a protocol."""

    def __init__(
        self,
        protocol: RpcProtocol,
        *,
        resolver: RpcResolver | None = None,
    ) -> None:
        self._protocol = protocol
        self._resolver = resolver if resolver is not None else EmptyResolver()
        _assert_executable(protocol)

    def parse_request(
        self,
        raw_request: object,
        *,
        envelope: RpcRequestEnvelope | None = None,
    ) -> RpcInvocation:
        """Validate a request, or reuse an `envelope` validated from `raw_request`."""
        if envelope is not None:
            request = envelope
        else:
            try:
                request = RpcRequestEnvelope.model_validate(raw_request)
            except ValidationError as error:
                raise RpcInvalidRequestError() from error
        method = self._protocol.method(request.method)
        return RpcInvocation(
            request=request,
            method=method,
            params=(
                method.params_default()
                if method.params_default is not None
                and "params" not in request.model_fields_set
                else _validated_params(method, request.params)
            ),
        )

    async def execute(self, invocation: RpcInvocation) -> Any:
        method = invocation.method
        function = method.function
        resolver_scope = method.resolver_scope
        if function is None or resolver_scope is None:
            raise ProtocolDefinitionError(f"RPC method {method.name} is not executable")

        async with resolver_scope(self._resolver) as resolver:
            arguments = await handler_arguments(method, invocation.params, resolver)
            result = await function(**arguments)
            return adapter(method.result).validate_python(result, from_attributes=True)


async def handler_arguments(
    definition: RpcMethodDefinition | RpcSubscriptionDefinition,
    params: BaseModel | None,
    resolver: RpcResolver,
) -> dict[str, Any]:
    """Return a handler's injected dependencies, params and path values."""
    arguments = {
        parameter.name: await resolver.resolve(parameter.dependency)
        for parameter in definition.injected_parameters
    }
    if params is not None:
        if definition.params_parameter is not None:
            arguments[definition.params_parameter] = params
        elif definition.params_fields:
            arguments.update(
                {name: getattr(params, name) for name in definition.params_fields}
            )
        else:
            raise ProtocolDefinitionError(
                f"RPC operation {definition.name} has no params parameter"
            )
    if definition.path_model is not None:
        connection = await resolver.resolve(RpcConnection)
        try:
            values = definition.path_model.model_validate(dict(connection.path_params))
        except ValidationError as error:
            raise ProtocolDefinitionError(
                f"RPC operation {definition.name} received invalid path values"
            ) from error
        arguments.update(
            {name: getattr(values, name) for name in definition.path_parameters}
        )
    return arguments


def _validated_params(
    method: RpcMethodDefinition,
    raw_params: dict[str, Any] | list[Any],
) -> BaseModel | None:
    if isinstance(raw_params, list):
        raise RpcInvalidParamsError(message="RPC methods require named params")
    if method.params is None:
        if raw_params:
            raise RpcInvalidParamsError(
                _unexpected_params_error(method, sorted(raw_params))
            )
        return None
    try:
        validated = adapter(method.params).validate_python(raw_params)
        if (
            method.params_origin is not None
            and type(validated) is not method.params_origin
        ):
            return method.params_origin.model_validate(
                validated.model_dump(by_alias=False), by_name=True
            )
    except ValidationError as error:
        raise RpcInvalidParamsError.from_validation_error(error) from error
    return cast(BaseModel, validated)


def _unexpected_params_error(
    method: RpcMethodDefinition,
    names: list[str],
) -> ValidationError:
    line_errors: list[InitErrorDetails] = [
        {
            "type": "extra_forbidden",
            "loc": (name,),
            "input": None,
        }
        for name in names
    ]
    return ValidationError.from_exception_data(method.name, line_errors)


def _assert_executable(protocol: RpcProtocol) -> None:
    missing = sorted(
        method.name for method in protocol.methods if method.function is None
    )
    if missing:
        raise ProtocolDefinitionError(
            f"RPC protocol has no functions for methods: {missing}"
        )
