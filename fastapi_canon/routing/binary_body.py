from dataclasses import dataclass

from fastapi_canon.errors import ConfigurationError
from fastapi_canon.response import normalize_media_type


@dataclass(frozen=True, slots=True)
class BinaryBody:
    """Declare a request stream on an async handler's AsyncIterator[bytes] parameter."""

    media_type: str

    def __post_init__(self) -> None:
        normalized = normalize_media_type(self.media_type)
        if normalized is None:
            raise ConfigurationError("BinaryBody requires a media_type")
        object.__setattr__(self, "media_type", normalized)
