import pytest
from fastapi import FastAPI

from fastapi_canon import Composition
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry


def test_telemetry_requires_native_fastapi_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    monkeypatch.delattr(app, "_telemetry")
    with pytest.raises(FeatureConfigurationError, match=r"FastAPI >=0\.143"):
        Composition(telemetry=Telemetry()).build(app)


def test_telemetry_rejects_changed_native_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    monkeypatch.setattr(app, "_telemetry", {})
    with pytest.raises(FeatureConfigurationError, match="fields changed"):
        Composition(telemetry=Telemetry()).build(app)
