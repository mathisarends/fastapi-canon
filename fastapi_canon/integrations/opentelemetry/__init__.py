"""Optional OpenTelemetry integration declared on Canon features."""

from .declarations import (
    Instrumentation,
    ResourceInstrumentation,
    Telemetry,
    instrument,
)

__all__ = ["Instrumentation", "ResourceInstrumentation", "Telemetry", "instrument"]
