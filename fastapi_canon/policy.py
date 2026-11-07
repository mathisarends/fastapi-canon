from collections.abc import Sequence
from dataclasses import dataclass

from fastapi.params import Depends

from fastapi_canon.error import Error, ErrorConfigurationError
from fastapi_canon.error.registry import AnyError
from fastapi_canon.error.validation import unique_instances


@dataclass(frozen=True, slots=True, init=False)
class RouterPolicy:
    """Reusable HTTP prerequisites and their declared error contracts.

    Dependencies execute through FastAPI; ``raises`` documents possible errors.
    Route classes remain a declaration-time choice on each router.
    """

    dependencies: tuple[Depends, ...]
    raises: tuple[AnyError, ...]

    def __init__(
        self,
        *,
        dependencies: Sequence[Depends] = (),
        raises: Sequence[AnyError] = (),
    ) -> None:
        raw_dependencies: object = dependencies
        if isinstance(raw_dependencies, str) or not isinstance(
            raw_dependencies, Sequence
        ):
            msg = "dependencies must be a sequence of Depends instances"
            raise ErrorConfigurationError(msg)
        normalized_dependencies = tuple(
            unique_instances(dependencies, Depends, parameter="dependencies")
        )
        normalized_raises = normalize_raises(raises, parameter="raises")
        object.__setattr__(self, "dependencies", normalized_dependencies)
        object.__setattr__(self, "raises", normalized_raises)


def validate_policy(value: RouterPolicy | None) -> None:
    raw: object = value
    if raw is not None and not isinstance(raw, RouterPolicy):
        msg = "policy must be a RouterPolicy instance or None"
        raise ErrorConfigurationError(msg)


def normalize_raises(
    values: Sequence[AnyError],
    *,
    parameter: str,
) -> tuple[AnyError, ...]:
    raw_values: object = values
    if isinstance(raw_values, str) or not isinstance(raw_values, Sequence):
        msg = f"{parameter} must be a sequence of Error instances"
        raise ErrorConfigurationError(msg)
    return tuple(unique_instances(values, Error, parameter=parameter))
