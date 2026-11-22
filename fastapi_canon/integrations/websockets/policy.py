from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi_canon.errors import ConfigurationError

if TYPE_CHECKING:
    from fastapi_canon.integrations.websockets.adapters.fastapi import FastApiResolver
    from fastapi_canon.integrations.websockets.connection import (
        RpcBeforeAccept,
        RpcRejections,
    )
    from fastapi_canon.integrations.websockets.dependencies import RpcResolverLike
    from fastapi_canon.integrations.websockets.errors import RpcRejectionDeclaration
    from fastapi_canon.integrations.websockets.middleware import RpcMiddlewareLike
    from fastapi_canon.integrations.websockets.server import RpcErrorMapper


@dataclass(frozen=True, slots=True, init=False)
class WebSocketPolicy:
    """Reusable connection configuration for one router's WebSocket declarations.

    Sequences are added before an endpoint's own values. An endpoint may set a
    scalar only when the policy leaves it unset; a conflict raises
    ``ConfigurationError``. ``extend`` applies the same rules to a new policy.
    """

    context: Callable[..., Awaitable[Any]] | None
    hooks: tuple[Callable[..., Any], ...]
    resolver: RpcResolverLike | FastApiResolver | None
    rejects: tuple[RpcRejectionDeclaration, ...]
    rejections: RpcRejections | None
    middleware: tuple[RpcMiddlewareLike, ...]
    error_mapper: RpcErrorMapper | None
    strict_errors: bool | None
    before_accept: RpcBeforeAccept | None
    _bases: tuple[WebSocketPolicy, ...] = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        context: Callable[..., Awaitable[Any]] | None = None,
        hooks: Sequence[Callable[..., Any]] = (),
        resolver: RpcResolverLike | FastApiResolver | None = None,
        rejects: Sequence[RpcRejectionDeclaration] = (),
        rejections: RpcRejections | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        error_mapper: RpcErrorMapper | None = None,
        strict_errors: bool | None = None,
        before_accept: RpcBeforeAccept | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "context": context,
            "resolver": resolver,
            "rejections": rejections,
            "error_mapper": error_mapper,
            "strict_errors": strict_errors,
            "before_accept": before_accept,
        }
        for name, items in (
            ("hooks", hooks),
            ("rejects", rejects),
            ("middleware", middleware),
        ):
            values[name] = _sequence(name, items)
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_bases", ())

    def extend(
        self,
        *,
        context: Callable[..., Awaitable[Any]] | None = None,
        hooks: Sequence[Callable[..., Any]] = (),
        resolver: RpcResolverLike | FastApiResolver | None = None,
        rejects: Sequence[RpcRejectionDeclaration] = (),
        rejections: RpcRejections | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        error_mapper: RpcErrorMapper | None = None,
        strict_errors: bool | None = None,
        before_accept: RpcBeforeAccept | None = None,
    ) -> WebSocketPolicy:
        """Return a new policy that adds to this one and covers its declarations."""
        additions = {
            name: value
            for name, value in {
                "context": context,
                "hooks": hooks,
                "resolver": resolver,
                "rejects": rejects,
                "rejections": rejections,
                "middleware": middleware,
                "error_mapper": error_mapper,
                "strict_errors": strict_errors,
                "before_accept": before_accept,
            }.items()
            if value is not None and not (name in _SEQUENCES and not value)
        }
        extended = WebSocketPolicy(**apply_websocket_policy(self, additions))
        object.__setattr__(extended, "_bases", (self, *self._bases))
        return extended


def apply_websocket_policy(
    policy: WebSocketPolicy | None, options: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge a policy into the connection options of one declaration."""
    merged = dict(options)
    if policy is None:
        return merged
    for name in _SEQUENCES:
        inherited = getattr(policy, name)
        if inherited:
            merged[name] = (*inherited, *_sequence(name, options.get(name, ())))
    for name in _SCALARS:
        inherited = getattr(policy, name)
        if inherited is None:
            continue
        if name in options:
            msg = f"{name}= is already set by the router's websocket_policy"
            raise ConfigurationError(msg)
        merged[name] = inherited
    return merged


def covers_websocket_policy(
    declared: WebSocketPolicy | None, required: WebSocketPolicy
) -> bool:
    """Whether a declaration's policy is ``required`` or extends it."""
    return declared is not None and any(
        candidate == required for candidate in (declared, *declared._bases)
    )


_SEQUENCES = ("hooks", "rejects", "middleware")
_SCALARS = (
    "context",
    "resolver",
    "rejections",
    "error_mapper",
    "strict_errors",
    "before_accept",
)


def _sequence(name: str, values: object) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        msg = f"{name}= must be a sequence"
        raise ConfigurationError(msg)
    return tuple(values)
