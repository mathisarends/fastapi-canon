"""Non-owning span filtering for the contrib HTTP client adapter."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanContext, Status, StatusCode

_ATTRIBUTES = frozenset(
    {
        "http.method",
        "http.request.method",
        "http.status_code",
        "http.response.status_code",
        "server.address",
        "server.port",
        "net.peer.name",
        "net.peer.port",
        "network.protocol.version",
        "http.flavor",
        "error.type",
    }
)


class _HttpProvider(trace.TracerProvider):
    def __init__(
        self, provider: trace.TracerProvider | None, *, capture_url: bool
    ) -> None:
        self._provider = provider
        self._attributes = _ATTRIBUTES | (
            {"http.url", "url.full"} if capture_url else set()
        )

    def get_tracer(
        self,
        instrumenting_module_name: str,
        instrumenting_library_version: str | None = None,
        schema_url: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> trace.Tracer:
        return _HttpTracer(
            trace.get_tracer(
                instrumenting_module_name,
                instrumenting_library_version,
                tracer_provider=self._provider,
                schema_url=schema_url,
                attributes=attributes,
            ),
            self._attributes,
        )


class _HttpTracer(trace.Tracer):
    def __init__(self, tracer: trace.Tracer, attributes: frozenset[str]) -> None:
        self._tracer = tracer
        self._attributes = attributes

    def start_span(self, *args: Any, **kwargs: Any) -> Span:
        kwargs["attributes"] = {
            key: value
            for key, value in (kwargs.get("attributes") or {}).items()
            if key in self._attributes
        }
        kwargs["record_exception"] = False
        kwargs["set_status_on_exception"] = False
        return _HttpSpan(self._tracer.start_span(*args, **kwargs), self._attributes)

    def start_as_current_span(self, *args: Any, **kwargs: Any) -> Any:
        return self._span(*args, **kwargs)

    @contextmanager
    def _span(self, *args: Any, **kwargs: Any) -> Iterator[Span]:
        end_on_exit = kwargs.pop("end_on_exit", True)
        span = self.start_span(*args, **kwargs)
        with trace.use_span(
            span,
            end_on_exit=end_on_exit,
            record_exception=False,
            set_status_on_exception=False,
        ):
            try:
                yield span
            except Exception as error:
                span.set_attribute("error.type", type(error).__qualname__)
                span.set_status(StatusCode.ERROR)
                raise


class _HttpSpan(Span):
    def __init__(self, span: Span, attributes: frozenset[str]) -> None:
        self._span = span
        self._attributes = attributes

    def end(self, end_time: int | None = None) -> None:
        self._span.end(end_time)

    def get_span_context(self) -> SpanContext:
        return self._span.get_span_context()

    def is_recording(self) -> bool:
        return self._span.is_recording()

    def set_attribute(self, key: str, value: Any) -> None:
        if key in self._attributes:
            self._span.set_attribute(key, value)

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> None:
        if name == "exception" and attributes and "exception.type" in attributes:
            self._span.add_event(
                name, {"exception.type": attributes["exception.type"]}, timestamp
            )

    def add_link(self, *args: Any, **kwargs: Any) -> None:
        self._span.add_link(*args, **kwargs)

    def update_name(self, name: str) -> None:
        self._span.update_name(name)

    def set_status(
        self, status: Status | StatusCode, description: str | None = None
    ) -> None:
        self._span.set_status(
            status.status_code if isinstance(status, Status) else status
        )

    def record_exception(
        self,
        exception: BaseException,
        attributes: Mapping[str, Any] | None = None,
        timestamp: int | None = None,
        escaped: bool = False,
    ) -> None:
        self.add_event(
            "exception", {"exception.type": type(exception).__qualname__}, timestamp
        )
