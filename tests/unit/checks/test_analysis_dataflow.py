import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

import fastapi_canon.cli as canon_cli

PRELUDE = """
import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Protocol

import httpx
from dishka import Provider, Scope, provide

from fastapi_canon import APIRouter, Error, Feature, Inject, ServiceScope

class Missing(Exception):
    pass

missing = Error(Missing, status=404, code="missing", title="Missing")
router = APIRouter()
feature = Feature(name="api", routers=[router], errors=[missing])

def fail() -> None:
    raise Missing()

class Service:
    async def run(self) -> None:
        fail()

class Other:
    async def run(self) -> None:
        pass

Factory = Callable[[str], Service]

class Holder:
    def __init__(self, factory: Factory) -> None:
        self._factory = factory

    async def go(self) -> None:
        await self._factory("x").run()
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[[str], dict[str, Any]]:
    def run(source: str) -> dict[str, Any]:
        (tmp_path / "api.py").write_text(
            dedent(PRELUDE) + dedent(source), encoding="utf-8"
        )
        canon_cli.main(["check", "errors", str(tmp_path), "--format", "json"])
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        [operation] = report["operations"]
        result: dict[str, Any] = operation
        return result

    return run


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            async def execute(operation: Callable[[], Awaitable[None]]) -> None:
                await operation()

            async def job(value: int) -> None:
                fail()

            @router.get("/")
            async def endpoint() -> None:
                await execute(partial(job, 1))
            """,
            id="argument-partial",
        ),
        pytest.param(
            """
            class Runner:
                async def execute(self, operation: Callable[[], Awaitable[None]]) -> None:
                    await operation()

            @router.get("/")
            async def endpoint(runner: Inject[Runner], service: Inject[Service]) -> None:
                await runner.execute(service.run)
            """,
            id="argument-bound-method",
        ),
        pytest.param(
            """
            class Cache:
                async def run(self, *, producer: Callable[[], Awaitable[None]]) -> None:
                    await asyncio.create_task(producer())

            @router.get("/")
            async def endpoint(cache: Inject[Cache], service: Inject[Service]) -> None:
                await cache.run(producer=lambda: service.run())
            """,
            id="argument-lambda",
        ),
        pytest.param(
            """
            class Port(ABC):
                @abstractmethod
                def clean(self) -> None: ...

            class Implementation(Port):
                def clean(self) -> None:
                    fail()

            @router.get("/")
            async def endpoint(port: Inject[Port]) -> None:
                await asyncio.to_thread(port.clean)
            """,
            id="argument-abstract-method",
        ),
        pytest.param(
            """
            class Browser:
                def __init__(self) -> None:
                    self._holder = Holder(self._open)

                def _open(self, url: str) -> Service:
                    return Service()

            @router.get("/")
            async def endpoint(browser: Inject[Browser]) -> None:
                await browser._holder.go()
            """,
            id="held-constructor-argument",
        ),
        pytest.param(
            """
            @dataclass
            class Cleanup:
                name: str
                action: Callable[[], Awaitable[None]]

            @router.get("/")
            async def endpoint(service: Inject[Service]) -> None:
                for cleanup in [Cleanup("a", service.run), Cleanup("b", action=service.run)]:
                    await cleanup.action()
            """,
            id="held-dataclass-field",
        ),
        pytest.param(
            """
            class AppProvider(Provider):
                @provide(scope=Scope.APP)
                def factory(self, service: Service) -> Factory:
                    def build(url: str) -> Service:
                        return service
                    return build

                @provide(scope=Scope.APP)
                def holder(self, factory: Factory) -> Holder:
                    return Holder(factory)

            @router.get("/")
            async def endpoint(holder: Inject[Holder]) -> None:
                await holder.go()
            """,
            id="dishka-provider-method",
        ),
        pytest.param(
            """
            class AppProvider(Provider):
                @provide(scope=Scope.APP)
                def factory(self) -> Factory:
                    return lambda url: Service()

                holder = provide(Holder, scope=Scope.APP)

            @router.get("/")
            async def endpoint(holder: Inject[Holder]) -> None:
                await holder.go()
            """,
            id="dishka-provided-class",
        ),
        pytest.param(
            """
            class Tracks(Protocol):
                def __call__(self, name: str) -> None: ...

            class AppProvider(Provider):
                @provide(scope=Scope.APP)
                def tracks(self) -> Tracks:
                    return lambda name: fail()

            @router.get("/")
            async def endpoint(tracks: Inject[Tracks]) -> None:
                tracks("x")
            """,
            id="dishka-callable-protocol",
        ),
        pytest.param(
            """
            @router.get("/")
            async def endpoint(scope: Inject[ServiceScope[Service]]) -> None:
                async with scope() as service:
                    await service.run()
            """,
            id="service-scope",
        ),
        pytest.param(
            """
            class UnitOfWork[T](ABC):
                @abstractmethod
                def __call__(self) -> AbstractAsyncContextManager[T]: ...

            class Sessions[T](UnitOfWork[T]):
                @asynccontextmanager
                async def __call__(self) -> AsyncGenerator[T]:
                    yield None

            @router.get("/")
            async def endpoint(sessions: Inject[UnitOfWork[Service]]) -> None:
                async with sessions() as service:
                    await service.run()
            """,
            id="generic-context-manager",
        ),
        pytest.param(
            """
            @router.get("/")
            async def endpoint(service: Service | None) -> None:
                current, service = service, None
                items = [current for _ in range(3)]
                for item in tuple(items):
                    await items[0].run()
            """,
            id="unpacked-and-contained",
        ),
        pytest.param(
            """
            @router.get("/")
            async def endpoint(value: object) -> None:
                if not isinstance(value, Service):
                    raise ValueError()
                await value.run()
            """,
            id="negative-isinstance-guard",
        ),
        pytest.param(
            """
            async def make() -> Service:
                return Service()

            @router.get("/")
            async def endpoint() -> None:
                try:
                    service = await make()
                except ValueError:
                    return
                await service.run()
            """,
            id="try-with-exiting-handlers",
        ),
        pytest.param(
            """
            class Row:
                @property
                def name(self) -> str:
                    fail()
                    return ""

            @router.get("/")
            async def endpoint(key: str) -> None:
                row: Row | None = httpx.get("x").json()
                getattr(row, key)
            """,
            id="annotated-library-result",
        ),
    ],
)
def test_values_reach_the_application_code_that_raises(
    analyze: Callable[[str], dict[str, Any]], source: str
) -> None:
    operation = analyze(source)

    [finding] = operation["diagnostics"]
    assert finding["code"] == "CAN001"
    assert finding["trace"][-1] == "api.fail"
    assert operation["complete"]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            async def execute(search: Callable[..., object]) -> None:
                search("x")

            @router.get("/")
            async def endpoint() -> None:
                async with httpx.AsyncClient() as client:
                    await execute(client.get)
            """,
            id="library-callable-argument",
        ),
        pytest.param(
            """
            import time

            class Clock:
                def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
                    self._now = now

                def read(self) -> float:
                    return self._now()

            @router.get("/")
            async def endpoint() -> None:
                Clock().read()
            """,
            id="library-default",
        ),
        pytest.param(
            """
            class Controller:
                async def cancel(self, request_id: int) -> None:
                    fail()

            @router.get("/")
            async def endpoint() -> None:
                task: asyncio.Task[None] | None = None
                task = asyncio.ensure_future(asyncio.sleep(1))
                task.cancel()
            """,
            id="annotated-local",
        ),
    ],
)
def test_library_values_are_complete(
    analyze: Callable[[str], dict[str, Any]], source: str
) -> None:
    operation = analyze(source)

    assert operation["diagnostics"] == []
    assert operation["complete"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        pytest.param(
            """
            async def execute(operation: Callable[[], Awaitable[None]]) -> None:
                await operation()

            @router.get("/")
            async def endpoint(operation: Callable[[], Awaitable[None]]) -> None:
                await execute(operation)
            """,
            "unresolved call: operation",
            id="unknown-argument",
        ),
        pytest.param(
            """
            @router.get("/")
            async def endpoint(holder: Inject[Holder]) -> None:
                await holder.go()
            """,
            "unresolved call: self._factory",
            id="never-constructed",
        ),
        pytest.param(
            """
            def register(value: object) -> None:
                pass

            register(Holder)

            @router.get("/")
            async def endpoint(holder: Inject[Holder]) -> None:
                await holder.go()
            """,
            "unresolved call: self._factory",
            id="escaped-class",
        ),
        pytest.param(
            """
            class AppProvider(Provider):
                holder = provide(Holder, scope=Scope.APP)

            @router.get("/")
            async def endpoint(holder: Inject[Holder]) -> None:
                await holder.go()
            """,
            "unresolved call: self._factory",
            id="unprovided-dependency",
        ),
        pytest.param(
            """
            class Controller:
                async def cancel(self, request_id: int) -> None:
                    fail()

            @router.get("/")
            async def endpoint() -> None:
                task = asyncio.ensure_future(asyncio.sleep(1))
                task.cancel(1)
            """,
            "ambiguous call task.cancel",
            id="matching-signature",
        ),
        pytest.param(
            """
            @router.get("/")
            async def endpoint() -> None:
                service = Other()
                try:
                    service = Service()
                except ValueError:
                    pass
                await service.run()
            """,
            "ambiguous call service.run",
            id="try-with-continuing-handler",
        ),
        pytest.param(
            """
            @router.get("/")
            async def endpoint() -> None:
                payload = httpx.get("x").json()
                await payload.run()
            """,
            "ambiguous call payload.run",
            id="untyped-library-result",
        ),
    ],
)
def test_unknown_values_stay_incomplete(
    analyze: Callable[[str], dict[str, Any]], source: str, message: str
) -> None:
    operation = analyze(source)

    assert any(
        item["code"] == "CAN003" and item["message"].startswith(message)
        for item in operation["diagnostics"]
    )
    assert not operation["complete"]
