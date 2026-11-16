from typing import Any

import pytest

from fastapi_canon import Feature
from fastapi_canon.contracts import ContractSelection


@pytest.mark.parametrize(
    ("values", "error", "message"),
    [
        ({}, ValueError, "needs features or paths"),
        ({"features": ()}, ValueError, "unique Feature"),
        ({"features": ("browser",)}, TypeError, "Feature objects, not names"),
        ({"paths": ()}, ValueError, "unique registered"),
        ({"paths": ("relative",)}, ValueError, "absolute registered"),
        ({"paths": ("/rpc", "/rpc")}, ValueError, "unique registered"),
        ({"paths": "/rpc"}, TypeError, "tuple of path templates, not a string"),
    ],
)
def test_selection_rejects_invalid_values(
    values: dict[str, Any], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        ContractSelection(**values)


def test_selection_rejects_duplicate_feature_identity() -> None:
    feature = Feature(name="browser")

    with pytest.raises(ValueError, match="unique Feature"):
        ContractSelection(features=(feature, feature))


def test_selection_rejects_a_single_feature_instead_of_a_tuple() -> None:
    with pytest.raises(TypeError, match="tuple of Feature objects, not one"):
        ContractSelection(features=Feature(name="browser"))  # type: ignore[arg-type]
