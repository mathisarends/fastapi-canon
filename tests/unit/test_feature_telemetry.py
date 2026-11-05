import pytest
from fastapi import FastAPI

import fastapi_canon.integrations.opentelemetry.native as native
from fastapi_canon import Composition, FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry


def test_telemetry_requires_native_fastapi_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    monkeypatch.delattr(app, "_telemetry")
    with pytest.raises(FeatureConfigurationError, match=r"FastAPI >=0\.143"):
        Composition(telemetry=Telemetry()).apply(app)


def test_telemetry_rejects_unsupported_fastapi_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "version", lambda name: "0.142.0")
    with pytest.raises(FeatureConfigurationError, match=r"FastAPI >=0\.143"):
        Composition(telemetry=Telemetry()).apply(FastAPI())


def test_telemetry_rejects_changed_native_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    monkeypatch.setattr(app, "_telemetry", {})
    with pytest.raises(FeatureConfigurationError, match="fields changed"):
        Composition(telemetry=Telemetry()).apply(app)
