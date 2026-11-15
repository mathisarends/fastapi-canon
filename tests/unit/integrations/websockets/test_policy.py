from typing import Any

import pytest
from fastapi_canon.exceptions import ConfigurationError
from fastapi_canon.integrations.websockets import (
    RpcErrorBinding,
    RpcMiddleware,
    RpcRejection,
    WebSocketPolicy,
)
from fastapi_canon.integrations.websockets.policy import apply_websocket_policy


class Unauthorized(Exception):
    pass


class Missing(Exception):
    pass


UNAUTHORIZED = RpcErrorBinding(Unauthorized, rejection=RpcRejection.UNAUTHORIZED)
MISSING = RpcErrorBinding(Missing, rejection=RpcRejection.NOT_FOUND)


async def context() -> object:
    return object()


def test_extend_adds_sequences_after_the_base_and_keeps_the_base() -> None:
    first, second = RpcMiddleware(), RpcMiddleware()
    base = WebSocketPolicy(context=context, rejects=[UNAUTHORIZED], middleware=[first])

    extended = base.extend(rejects=[MISSING], middleware=[second], strict_errors=False)

    assert extended.rejects == (UNAUTHORIZED, MISSING)
    assert extended.middleware == (first, second)
    assert extended.context is context
    assert extended.strict_errors is False
    assert base.rejects == (UNAUTHORIZED,)
    assert base.strict_errors is None


def test_declarations_add_to_policy_sequences_and_fill_unset_scalars() -> None:
    policy = WebSocketPolicy(context=context, rejects=[UNAUTHORIZED])

    options = apply_websocket_policy(policy, {"rejects": [MISSING], "version": 2})

    assert options == {
        "context": context,
        "rejects": (UNAUTHORIZED, MISSING),
        "version": 2,
    }


@pytest.mark.parametrize(
    "options",
    [{"context": None}, {"strict_errors": False}],
    ids=["explicit-none", "explicit-false"],
)
def test_declarations_cannot_replace_a_policy_scalar(options: dict[str, Any]) -> None:
    policy = WebSocketPolicy(context=context, strict_errors=True)

    with pytest.raises(ConfigurationError, match="already set"):
        apply_websocket_policy(policy, options)


def test_extend_cannot_replace_a_policy_scalar() -> None:
    policy = WebSocketPolicy(strict_errors=True)

    with pytest.raises(ConfigurationError, match="strict_errors"):
        policy.extend(strict_errors=False)


def test_policy_sequences_reject_strings() -> None:
    with pytest.raises(ConfigurationError, match="rejects"):
        WebSocketPolicy(rejects="unauthorized")  # type: ignore[arg-type]
