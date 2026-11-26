from collections.abc import AsyncIterator

import pytest
from fastapi_canon.integrations.websockets import ProtocolDefinitionError, RpcChannel


def test_anonymous_channels_have_unprefixed_operations() -> None:
    channel = RpcChannel()

    @channel.method("rename")
    async def rename() -> str:
        return "renamed"

    assert channel.namespace == ""
    assert [method.name for method in channel.freeze().methods] == ["rename"]


def test_included_channels_keep_their_namespace_under_the_parent() -> None:
    projects = RpcChannel(namespace="projects")
    tasks = projects.child("tasks")

    @projects.method("rename")
    async def rename() -> None:
        return None

    @tasks.method("create")
    async def create() -> None:
        return None

    session = RpcChannel()
    session.include_channel(projects)
    workspace = RpcChannel(namespace="workspace")
    workspace.include_channel(projects)
    assert [method.name for method in session.freeze().methods] == [
        "projects.rename",
        "projects.tasks.create",
    ]
    assert [method.name for method in workspace.freeze().methods] == [
        "workspace.projects.rename",
        "workspace.projects.tasks.create",
    ]


def test_inclusion_snapshots_without_freezing_the_source() -> None:
    source = RpcChannel(namespace="tasks")

    @source.method("first")
    async def first() -> None:
        return None

    session = RpcChannel()
    session.include_channel(source)

    @source.method("later")
    async def later() -> None:
        return None

    assert [method.name for method in session.freeze().methods] == ["tasks.first"]
    assert [method.name for method in source.freeze().methods] == [
        "tasks.first",
        "tasks.later",
    ]


def test_duplicate_inclusion_fails_atomically() -> None:
    source = RpcChannel(namespace="tasks")

    @source.method("read")
    async def read() -> None:
        return None

    session = RpcChannel()
    session.include_channel(source)
    with pytest.raises(ProtocolDefinitionError, match="Duplicate RPC route"):
        session.include_channel(source)
    assert [method.name for method in session.freeze().methods] == ["tasks.read"]


def test_operations_cannot_become_included_namespaces() -> None:
    source = RpcChannel(namespace="tasks")

    @source.method("read")
    async def read() -> None:
        return None

    session = RpcChannel()

    @session.method("tasks")
    async def tasks() -> None:
        return None

    with pytest.raises(ProtocolDefinitionError, match="operation namespace"):
        session.include_channel(source)
    assert [method.name for method in session.freeze().methods] == ["tasks"]


def test_message_stream_names_and_controls_are_composed() -> None:
    source = RpcChannel(namespace="projects")

    @source.stream("events")
    async def events() -> AsyncIterator[str]:
        yield "updated"

    session = RpcChannel()
    session.include_channel(source)

    async def shadow() -> None:
        return None

    with pytest.raises(ProtocolDefinitionError, match="Duplicate RPC route"):
        session.method("projects.events.open")(shadow)
    assert [stream.name for stream in session.freeze().subscriptions] == [
        "projects.events"
    ]


def test_channels_cannot_include_themselves() -> None:
    channel = RpcChannel()
    with pytest.raises(ProtocolDefinitionError, match="itself"):
        channel.include_channel(channel)
    assert channel.children == ()


@pytest.mark.parametrize("namespace", [None, 4, "bad/name", "two..parts"])
def test_channels_reject_invalid_namespaces(namespace: object) -> None:
    with pytest.raises(ProtocolDefinitionError, match="namespace"):
        RpcChannel(namespace=namespace)  # type: ignore[arg-type]  # Invalid namespace under test.
