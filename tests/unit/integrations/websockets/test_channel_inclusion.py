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


def test_included_channels_replace_root_and_preserve_nested_namespaces() -> None:
    projects = RpcChannel("original")
    tasks = projects.child("tasks")

    @projects.method("rename")
    async def rename() -> None:
        return None

    @tasks.method("create")
    async def create() -> None:
        return None

    session = RpcChannel()
    session.include_channel(projects, namespace="projects")
    assert [method.name for method in session.freeze().methods] == [
        "projects.rename",
        "projects.tasks.create",
    ]
    assert [method.name for method in projects.freeze().methods] == [
        "original.rename",
        "original.tasks.create",
    ]


def test_inclusion_snapshots_without_freezing_the_source() -> None:
    source = RpcChannel()

    @source.method("first")
    async def first() -> None:
        return None

    session = RpcChannel()
    session.include_channel(source, namespace="tasks")

    @source.method("later")
    async def later() -> None:
        return None

    assert [method.name for method in session.freeze().methods] == ["tasks.first"]
    assert [method.name for method in source.freeze().methods] == ["first", "later"]


def test_the_same_channel_can_be_included_under_two_namespaces() -> None:
    source = RpcChannel()

    @source.method("read")
    async def read() -> None:
        return None

    session = RpcChannel()
    session.include_channel(source, namespace="first")
    session.include_channel(source, namespace="second")
    assert [method.name for method in session.freeze().methods] == [
        "first.read",
        "second.read",
    ]


def test_duplicate_inclusion_fails_atomically() -> None:
    source = RpcChannel()

    @source.method("read")
    async def read() -> None:
        return None

    session = RpcChannel()
    session.include_channel(source, namespace="tasks")
    with pytest.raises(ProtocolDefinitionError, match="Duplicate RPC route"):
        session.include_channel(source, namespace="tasks")
    assert [method.name for method in session.freeze().methods] == ["tasks.read"]


def test_operations_cannot_become_included_namespaces() -> None:
    source = RpcChannel()

    @source.method("read")
    async def read() -> None:
        return None

    session = RpcChannel()

    @session.method("tasks")
    async def tasks() -> None:
        return None

    with pytest.raises(ProtocolDefinitionError, match="operation namespace"):
        session.include_channel(source, namespace="tasks")
    assert [method.name for method in session.freeze().methods] == ["tasks"]


def test_message_stream_names_and_controls_are_composed() -> None:
    source = RpcChannel()

    @source.stream("events")
    async def events() -> AsyncIterator[str]:
        yield "updated"

    session = RpcChannel()
    session.include_channel(source, namespace="projects")

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
def test_inclusion_rejects_invalid_namespaces(namespace: object) -> None:
    channel = RpcChannel()
    with pytest.raises(ProtocolDefinitionError, match="namespace"):
        channel.include_channel(RpcChannel(), namespace=namespace)  # type: ignore[arg-type]
    assert channel.children == ()
