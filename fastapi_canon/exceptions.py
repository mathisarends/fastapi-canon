"""Configuration failures shared by all Canon subsystems."""

__all__ = [
    "ConfigurationError",
    "ErrorConfigurationError",
    "FeatureConfigurationError",
    "ResponseConfigurationError",
]


class ConfigurationError(ValueError):
    """A Canon declaration cannot form a coherent application contract."""


class ErrorConfigurationError(ConfigurationError):
    """Raised when error definitions cannot form a safe, coherent contract."""


class FeatureConfigurationError(ErrorConfigurationError):
    """Raised when feature contributions cannot be installed coherently."""


class ResponseConfigurationError(ErrorConfigurationError):
    """Raised when a Canon response conflicts with its FastAPI route."""
