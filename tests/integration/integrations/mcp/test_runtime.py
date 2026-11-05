import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from dishka import Provider, Scope, make_async_container, provide
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.utilities.asgi_transport import StreamingASGITransport
from mcp import MCPError

from fastapi_canon import Composition, Feature, FeatureConfigurationError
from fastapi_canon.integrations.mcp import (
    Inject,
    McpMount,
    McpOptions,
    McpScope,
    McpServer,
)


class Service:
    def __init__(self) -> None:
        self.identifier = str(uuid4())


class Services(Provider):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    @provide(scope=Scope.REQUEST)
    async def service(self) -> AsyncGenerator[Service, BaseException | None]:
        self.events.append("open")
        try:
            exception = yield Service()
            if exception is not None:
                self.events.append("rollback")
            else:
                self.events.append("commit")
        except BaseException:
            self.events.append("rollback")
            raise
        finally:
            self.events.append("close")


@asynccontextmanager
async def connected(app: FastAPI, path: str = "/mcp") -> AsyncIterator[Client[Any]]:
    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kwargs: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=StreamingASGITransport(app),
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    transport = StreamableHttpTransport(
        "http://testserver" + path, httpx_client_factory=factory
    )
    async with app.router.lifespan_context(app), Client(transport, timeout=5) as client:
        yield client


def application(server: FastMCP, events: list[str], path: str = "/mcp") -> FastAPI:
    feature = Feature(
        name="portfolio",
        providers=[lambda: Services(events)],
        mcp=[McpMount(server=server, namespace="portfolio")],
    )
    return Composition(features=[feature], mcp=McpOptions(path=path)).apply(
        FastAPI(title="Maximus")
    )


async def test_schema_and_shared_request_scope() -> None:
    events: list[str] = []
    server = McpServer("Portfolio")

    @server.tool
    async def get(
        portfolio_id: str, first: Inject[Service], second: Inject[Service]
    ) -> str:
        assert first is second
        return portfolio_id + first.identifier

    async with connected(application(server, events)) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["portfolio_get"]
        assert tools[0].input_schema["required"] == ["portfolio_id"]
        assert set(tools[0].input_schema["properties"]) == {"portfolio_id"}
        result = await client.call_tool("portfolio_get", {"portfolio_id": "p1"})
        assert result.data.startswith("p1")
    assert events == ["open", "commit", "close"]


async def test_concurrent_calls_have_independent_scopes() -> None:
    events: list[str] = []
    server = McpServer("Portfolio")

    @server.tool(name="identify")
    async def identify(service: Inject[Service]) -> str:
        await asyncio.sleep(0)
        return service.identifier

    async with connected(application(server, events)) as client:
        async with asyncio.timeout(5):
            results = await asyncio.gather(
                *(client.call_tool("portfolio_identify") for _ in range(4))
            )
        assert len({result.data for result in results}) == 4
    assert events.count("open") == events.count("close") == 4


async def test_handler_failure_rolls_back_before_mcp_error() -> None:
    events: list[str] = []
    server = McpServer("Portfolio")

    @server.tool
    async def fail(service: Inject[Service]) -> str:
        raise ValueError(service.identifier)

    async with connected(application(server, events)) as client:
        result = await client.call_tool("portfolio_fail", raise_on_error=False)
        assert result.is_error
    assert events == ["open", "rollback", "close"]


async def test_same_feature_server_can_serve_independent_apps() -> None:
    events_a: list[str] = []
    events_b: list[str] = []
    server = McpServer("Portfolio")

    @server.tool
    async def identify(service: Inject[Service]) -> str:
        return service.identifier

    async with (
        connected(application(server, events_a)) as a,
        connected(application(server, events_b, "/agent"), "/agent") as b,
    ):
        first = await a.call_tool("portfolio_identify")
        second = await b.call_tool("portfolio_identify")
        assert first.data != second.data
    assert events_a == events_b == ["open", "commit", "close"]


async def test_resources_and_prompts_use_dishka() -> None:
    events: list[str] = []
    server = McpServer("Portfolio")

    @server.resource("portfolio://current")
    async def current(service: Inject[Service]) -> str:
        return service.identifier

    @server.prompt
    async def describe(service: Inject[Service]) -> str:
        return "Portfolio " + service.identifier

    async with connected(application(server, events)) as client:
        resources = await client.read_resource("portfolio://portfolio/current")
        prompt = await client.get_prompt("portfolio_describe")
        assert len(resources) == 1
        assert len(prompt.messages) == 1
    assert events.count("open") == events.count("close") == 2


def test_mounts_require_explicit_composition_options() -> None:
    feature = Feature(
        name="portfolio",
        mcp=[McpMount(server=FastMCP("Portfolio"), namespace="portfolio")],
    )
    with pytest.raises(FeatureConfigurationError, match="McpOptions"):
        Composition(features=[feature]).apply(FastAPI())


def test_duplicate_namespaces_are_rejected() -> None:
    feature = Feature(
        name="portfolio",
        mcp=[
            McpMount(server=FastMCP("First"), namespace="portfolio"),
            McpMount(server=FastMCP("Second"), namespace="portfolio"),
        ],
    )
    with pytest.raises(FeatureConfigurationError, match="Duplicate MCP"):
        Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())


def test_background_tools_are_rejected_explicitly() -> None:
    server = McpServer("Portfolio")
    with pytest.raises(ValueError, match="background"):
        server.tool(task=True)


async def test_native_fastmcp_tools_need_no_providers() -> None:
    server = FastMCP("Native")

    @server.tool
    async def greet(name: str) -> str:
        return "Hello " + name

    feature = Feature(name="native", mcp=[McpMount(server=server, namespace="native")])
    app = Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())
    async with connected(app) as client:
        result = await client.call_tool("native_greet", {"name": "Canon"})
        assert result.data == "Hello Canon"


async def test_mcp_lifespan_runs_once_inside_container_lifetime() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[None]:
        events.append("startup")
        yield
        events.append("shutdown")

    server = McpServer("Portfolio", lifespan=lifespan)
    async with connected(application(server, events)) as client:
        assert await client.list_tools() == []
        assert events == ["startup"]
    assert events == ["startup", "shutdown"]


async def test_composition_is_idempotent_with_mcp() -> None:
    feature = Feature(
        name="native", mcp=[McpMount(server=FastMCP("Native"), namespace="native")]
    )
    composition = Composition(features=[feature], mcp=McpOptions())
    app = composition.apply(FastAPI())
    assert composition.apply(app) is app
    assert [getattr(route, "path", None) for route in app.routes].count("/mcp") == 1
    with pytest.raises(FeatureConfigurationError, match="different composition"):
        Composition(features=[feature], mcp=McpOptions(path="/other")).apply(app)


async def test_response_serialization_failure_rolls_back() -> None:
    events: list[str] = []
    server = McpServer("Portfolio")

    @server.tool
    async def invalid(service: Inject[Service]) -> dict[str, object]:
        return {"invalid": service}

    async with connected(application(server, events)) as client:
        result = await client.call_tool("portfolio_invalid", raise_on_error=False)
        assert result.is_error
    assert events == ["open", "rollback", "close"]


async def test_cancelled_handler_closes_scope() -> None:
    events: list[str] = []
    server = McpServer("Portfolio")

    @server.tool
    async def cancelled(service: Inject[Service]) -> str:
        assert service.identifier
        raise asyncio.CancelledError

    async with connected(application(server, events)) as client:
        with pytest.raises(MCPError, match="Server returned an error response"):
            async with asyncio.timeout(0.5):
                await client.call_tool("portfolio_cancelled")
    assert events == ["open", "rollback", "close"]


async def test_shared_database_transactions_commit_and_rollback() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from fastapi_canon import Database

    class Store:
        def __init__(self, session: AsyncSession) -> None:
            self.session = session

    class StoreProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def store(self, session: AsyncSession) -> Store:
            return Store(session)

    server = McpServer("Database")

    async def insert(value: int, store: Inject[Store]) -> int:
        await store.session.execute(
            text("INSERT INTO entries VALUES (:value)"), {"value": value}
        )
        if value < 0:
            raise ValueError("negative")
        return value

    server.add_tool(insert)
    database = Database(url="sqlite:///:memory:")
    feature = Feature(
        name="store",
        providers=[StoreProvider],
        mcp=[McpMount(server=server, namespace="store")],
    )
    app = Composition(features=[database.feature, feature], mcp=McpOptions()).apply(
        FastAPI()
    )
    async with connected(app) as client:
        engine = await app.state.dishka_container.get(AsyncEngine)
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE entries (value INTEGER)"))
        success = await client.call_tool("store_insert", {"value": 7})
        failure = await client.call_tool(
            "store_insert", {"value": -1}, raise_on_error=False
        )
        assert success.data == 7
        assert failure.is_error
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT value FROM entries"))
            assert list(rows.scalars()) == [7]


def test_child_http_auth_is_rejected_instead_of_silently_ignored() -> None:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    server = FastMCP(
        "Protected",
        auth=StaticTokenVerifier(
            tokens={"secret": {"client_id": "test", "scopes": []}}
        ),
    )
    feature = Feature(
        name="protected", mcp=[McpMount(server=server, namespace="protected")]
    )
    with pytest.raises(FeatureConfigurationError, match="McpOptions"):
        Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())


async def test_root_http_auth_rejects_unauthenticated_requests() -> None:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    auth = StaticTokenVerifier(tokens={"secret": {"client_id": "test", "scopes": []}})
    app = Composition(mcp=McpOptions(auth=auth)).apply(FastAPI())
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=StreamingASGITransport(app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert response.status_code == 401


async def test_public_scope_shares_native_injection_and_restores_nested_context() -> (
    None
):
    events: list[str] = []
    container = make_async_container(Services(events))
    scope = McpScope(container)
    server = McpServer("Operations")

    @server.tool
    async def identify(service: Inject[Service]) -> str:
        return service.identifier

    try:
        async with scope() as outer:
            first = await outer.get(Service)
            assert (await server.call_tool("identify")).structured_content == {
                "result": first.identifier
            }
            async with scope() as inner:
                second = await inner.get(Service)
                assert second is not first
                assert (await server.call_tool("identify")).structured_content == {
                    "result": second.identifier
                }
            assert (await server.call_tool("identify")).structured_content == {
                "result": first.identifier
            }
        assert events == ["open", "open", "commit", "close", "commit", "close"]
        async with scope() as fresh:
            assert (await fresh.get(Service)).identifier != first.identifier
    finally:
        await container.close()


async def test_shielded_operation_owns_resources_after_its_waiter_is_cancelled() -> (
    None
):
    events: list[str] = []
    container = make_async_container(Services(events))
    scope = McpScope(container)
    started, finish = asyncio.Event(), asyncio.Event()

    async def execute() -> str:
        async with scope() as scoped:
            service = await scoped.get(Service)
            started.set()
            async with asyncio.timeout(5):
                await finish.wait()
            assert events == ["open"]
            return service.identifier

    task = asyncio.create_task(execute())

    async def wait_for_result() -> str:
        return await asyncio.shield(task)

    waiter = asyncio.create_task(wait_for_result())
    try:
        async with asyncio.timeout(5):
            await started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not task.done()
            finish.set()
            assert await task
        assert events == ["open", "commit", "close"]
    finally:
        finish.set()
        task.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)
        await container.close()


async def test_failed_public_operation_scope_rolls_back_and_remains_reusable() -> None:
    events: list[str] = []
    container = make_async_container(Services(events))
    scope = McpScope(container)
    try:
        with pytest.raises(ValueError, match="operation rejected"):
            async with scope() as scoped:
                await scoped.get(Service)
                raise ValueError("operation rejected")
        assert events == ["open", "rollback", "close"]
        async with scope() as scoped:
            assert (await scoped.get(Service)).identifier
        assert events == ["open", "rollback", "close", "open", "commit", "close"]
    finally:
        await container.close()


@pytest.mark.parametrize("stateless", [False, True])
async def test_http_session_mode_is_explicitly_configurable(stateless: bool) -> None:
    app = Composition(mcp=McpOptions(stateless_http=stateless)).apply(FastAPI())
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=StreamingASGITransport(app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )

    assert response.status_code == 200
    assert bool(response.headers.get("mcp-session-id")) is not stateless


async def test_root_middleware_wraps_children_before_dependency_cleanup() -> None:
    events: list[str] = []

    class Recording(Middleware):
        def __init__(self, name: str) -> None:
            self.name = name

        async def on_call_tool(
            self, context: MiddlewareContext[Any], call_next: Any
        ) -> Any:
            events.append(self.name + ".before")
            result = await call_next(context)
            events.append(self.name + ".after")
            return result

    server = McpServer("Portfolio", middleware=[Recording("child")])

    @server.tool
    async def identify(service: Inject[Service]) -> str:
        events.append("handler")
        return service.identifier

    feature = Feature(
        name="portfolio",
        providers=[lambda: Services(events)],
        mcp=[McpMount(server=server, namespace="portfolio")],
    )
    app = Composition(
        features=[feature],
        mcp=McpOptions(middleware=(Recording("root"),)),
    ).apply(FastAPI())

    async with connected(app) as client:
        assert (await client.call_tool("portfolio_identify")).data
    assert events == [
        "root.before",
        "child.before",
        "open",
        "handler",
        "child.after",
        "root.after",
        "commit",
        "close",
    ]


@pytest.mark.parametrize("masked", [False, True])
async def test_root_masks_forwarded_execution_failures_when_requested(
    masked: bool,
) -> None:
    class Failure(Middleware):
        async def on_call_tool(
            self, context: MiddlewareContext[Any], call_next: Any
        ) -> Any:
            raise RuntimeError("private-operation-detail")

    server = FastMCP("Native", middleware=[Failure()])

    @server.tool
    async def greet() -> str:
        return "hello"

    app = Composition(
        features=[
            Feature(name="native", mcp=[McpMount(server=server, namespace="native")])
        ],
        mcp=McpOptions(mask_error_details=masked),
    ).apply(FastAPI())

    async with connected(app) as client:
        result = await client.call_tool("native_greet", raise_on_error=False)
    assert result.is_error
    assert ("private-operation-detail" in str(result.content)) is not masked


async def test_concatenated_tool_names_cannot_be_discovered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first, second = FastMCP("First"), FastMCP("Second")

    @first.tool(name="b_c")
    async def one() -> str:
        return "first"

    @second.tool(name="c")
    async def two() -> str:
        return "second"

    feature = Feature(
        name="collision",
        mcp=[
            McpMount(server=first, namespace="a"),
            McpMount(server=second, namespace="a_b"),
        ],
    )
    app = Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())

    async with connected(app) as client:
        with pytest.raises(MCPError, match="Internal server error"):
            await client.list_tools()
    assert "Duplicate MCP component 'tool:a_b_c@'" in caplog.text


async def test_ambiguous_tool_call_is_rejected_before_either_handler_runs() -> None:
    calls: list[str] = []
    first, second = FastMCP("First"), FastMCP("Second")

    @first.tool(name="b_c")
    async def one() -> str:
        calls.append("first")
        return "first"

    @second.tool(name="c")
    async def two() -> str:
        calls.append("second")
        return "second"

    feature = Feature(
        name="collision",
        mcp=[
            McpMount(server=first, namespace="a"),
            McpMount(server=second, namespace="a_b"),
        ],
    )
    app = Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())

    async with connected(app) as client:
        with pytest.raises(MCPError, match="Internal server error"):
            await client.call_tool("a_b_c", raise_on_error=False)
    assert calls == []


async def test_concatenated_prompt_names_cannot_be_discovered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first, second = FastMCP("First"), FastMCP("Second")

    @first.prompt(name="b_c")
    async def one() -> str:
        return "first"

    @second.prompt(name="c")
    async def two() -> str:
        return "second"

    feature = Feature(
        name="collision",
        mcp=[
            McpMount(server=first, namespace="a"),
            McpMount(server=second, namespace="a_b"),
        ],
    )
    app = Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())

    async with connected(app) as client:
        with pytest.raises(MCPError, match="Internal server error"):
            await client.list_prompts()
    assert "Duplicate MCP component 'prompt:a_b_c@'" in caplog.text


async def test_distinct_versions_of_a_public_tool_name_remain_supported() -> None:
    first, second = FastMCP("First"), FastMCP("Second")

    @first.tool(name="b_c", version="1")
    async def one() -> str:
        return "first"

    @second.tool(name="c", version="2")
    async def two() -> str:
        return "second"

    feature = Feature(
        name="versions",
        mcp=[
            McpMount(server=first, namespace="a"),
            McpMount(server=second, namespace="a_b"),
        ],
    )
    app = Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())

    async with connected(app) as client:
        assert [tool.name for tool in await client.list_tools()] == ["a_b_c"]
        assert (await client.call_tool("a_b_c")).data == "second"


async def test_late_tool_registration_cannot_introduce_an_ambiguous_call() -> None:
    first, second = FastMCP("First"), FastMCP("Second")
    calls: list[str] = []

    @first.tool(name="b_c")
    async def one() -> str:
        calls.append("first")
        return "first"

    feature = Feature(
        name="dynamic",
        mcp=[
            McpMount(server=first, namespace="a"),
            McpMount(server=second, namespace="a_b"),
        ],
    )
    app = Composition(features=[feature], mcp=McpOptions()).apply(FastAPI())

    async with connected(app) as client:
        assert [tool.name for tool in await client.list_tools()] == ["a_b_c"]

        @second.tool(name="c")
        async def two() -> str:
            calls.append("second")
            return "second"

        with pytest.raises(MCPError, match="Internal server error"):
            await client.call_tool("a_b_c", raise_on_error=False)
    assert calls == []
