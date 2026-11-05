import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from abc import ABC, abstractmethod
from typing import Protocol
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Error, ErrorRegistry, Inject

class Missing(Exception):
    pass

class Conflict(Exception):
    pass

missing = Error(Missing, status=404, code="missing", title="Missing")
conflict = Error(Conflict, status=409, code="conflict", title="Conflict")
router = APIRouter(error_registry=ErrorRegistry(errors=[missing, conflict]))

class Repository(ABC):
    @abstractmethod
    async def get(self) -> None: ...

class Service:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    async def run(self) -> None:
        await self._repository.get()
"""

ENDPOINT = """
@router.get('/', {declaration})
async def endpoint(service: Inject[Service]) -> None:
    await service.run()
"""

SQL_REPOSITORY = """
class SqlRepository(Repository):
    async def get(self) -> None:
        raise Missing()
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., dict[str, Any]]:
    def run(source: str, declaration: str = "") -> dict[str, Any]:
        endpoint = ENDPOINT.format(declaration=declaration)
        (tmp_path / "api.py").write_text(
            dedent(PRELUDE + source + endpoint), encoding="utf-8"
        )
        main(["check", "errors", str(tmp_path), "--format", "json"])
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        return report

    return run


def diagnostics(report: dict[str, Any]) -> list[dict[str, Any]]:
    [operation] = report["operations"]
    return list(operation["diagnostics"])


@pytest.mark.parametrize(
    "provider",
    [
        """
class AppProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self) -> Repository:
        return SqlRepository()
""",
        """
class AppProvider(Provider):
    repository = provide(SqlRepository, provides=Repository, scope=Scope.REQUEST)
""",
        """
Alias = SqlRepository

class AppProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self) -> Repository:
        return Alias()
""",
    ],
    ids=["factory", "provides", "module-alias"],
)
def test_port_calls_follow_the_dishka_binding(
    analyze: Callable[..., dict[str, Any]], provider: str
) -> None:
    report = analyze(SQL_REPOSITORY + provider)

    [finding] = diagnostics(report)
    assert finding["code"] == "CAN001"
    assert finding["trace"] == [
        "api.endpoint",
        "api.Service.run",
        "api.SqlRepository.get",
    ]
    assert report["operations"][0]["complete"]


def test_declared_errors_of_the_implementation_complete_the_check(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    provider = """
class AppProvider(Provider):
    repository = provide(SqlRepository, provides=Repository, scope=Scope.REQUEST)
"""

    report = analyze(SQL_REPOSITORY + provider, declaration="raises=[missing]")

    assert diagnostics(report) == []
    assert report["operations"][0]["complete"]


def test_multiple_bindings_contribute_the_union_of_their_errors(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    providers = """
class MemoryRepository(Repository):
    async def get(self) -> None:
        raise Conflict()

class AppProvider(Provider):
    repository = provide(SqlRepository, provides=Repository, scope=Scope.REQUEST)

class TestProvider(AppProvider):
    repository = provide(
        MemoryRepository, provides=Repository, scope=Scope.REQUEST, override=True
    )
"""

    report = analyze(SQL_REPOSITORY + providers, declaration="raises=[missing]")

    [finding] = diagnostics(report)
    assert finding["code"] == "CAN001"
    assert finding["trace"][-1] == "api.MemoryRepository.get"


def test_protocol_ports_follow_the_dishka_binding(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    source = """
class Clock(Protocol):
    def now(self) -> int: ...

class BrokenClock:
    def now(self) -> int:
        raise Conflict()

class SqlRepository(Repository):
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def get(self) -> None:
        self._clock.now()

class AppProvider(Provider):
    repository = provide(SqlRepository, provides=Repository, scope=Scope.REQUEST)
    clock = provide(BrokenClock, provides=Clock, scope=Scope.APP)
"""

    report = analyze(source)

    [finding] = diagnostics(report)
    assert finding["trace"][-1] == "api.BrokenClock.now"


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("", "abstract implementation without Dishka binding: api.Repository.get"),
        (
            """
def build() -> Repository:
    return SqlRepository()

class AppProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self) -> Repository:
        return build()
""",
            "unresolved Dishka implementation of api.Repository.get",
        ),
    ],
    ids=["unbound", "opaque-factory"],
)
def test_unresolved_ports_stay_incomplete(
    analyze: Callable[..., dict[str, Any]], provider: str, message: str
) -> None:
    report = analyze(SQL_REPOSITORY + provider, declaration="raises=[missing]")

    assert [(item["code"], item["message"]) for item in diagnostics(report)] == [
        ("CAN003", message)
    ]
    assert not report["operations"][0]["complete"]


def test_abstract_self_calls_dispatch_to_concrete_subclasses(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    source = """
class Template(Repository):
    async def get(self) -> None:
        self.load()

    @abstractmethod
    def load(self) -> None: ...

class Concrete(Template):
    def load(self) -> None:
        raise Missing()

class AppProvider(Provider):
    repository = provide(Concrete, provides=Repository, scope=Scope.REQUEST)
"""

    report = analyze(source)

    [finding] = diagnostics(report)
    assert finding["code"] == "CAN001"
    assert finding["trace"][-2:] == ["api.Template.get", "api.Concrete.load"]


@pytest.mark.parametrize(
    "body",
    [
        "impl = SafeRepository()\nimpl = SqlRepository()\nreturn impl",
        "impl: Repository = SafeRepository()\nimpl = SqlRepository()\nreturn impl",
        "impl = SafeRepository()\nimpl: Repository = SqlRepository()\nreturn impl",
        "impl = SqlRepository()\nalias = impl\nimpl = SafeRepository()\nreturn alias",
        "impl = SafeRepository()\nif self.flag:\n    impl = SqlRepository()\nreturn impl",
        "if self.flag:\n    return SqlRepository()\nreturn SafeRepository()",
        "impl = SqlRepository()\nreturn impl\nimpl = SafeRepository()",
        "impl = SqlRepository()\nyield impl\nimpl = SafeRepository()",
        "impl = SafeRepository()\nimpl = SqlRepository()\nyield impl",
        "impl = SafeRepository()\nif self.flag:\n    impl = SqlRepository()\nyield impl",
    ],
    ids=[
        "reassignment",
        "annotated-initial",
        "annotated-reassignment",
        "local-alias",
        "conditional-assignment",
        "conditional-return",
        "unreachable-assignment",
        "yield-position",
        "reassigned-yield",
        "conditional-yield",
    ],
)
def test_factory_values_follow_assignment_and_exit_order(
    analyze: Callable[..., dict[str, Any]], body: str
) -> None:
    provider = (
        """
class SafeRepository(Repository):
    async def get(self) -> None:
        return None

class AppProvider(Provider):
    @provide(scope=Scope.REQUEST, provides=Repository)
    def repository(self):
"""
        + "\n".join("        " + line for line in body.splitlines())
        + "\n"
    )

    report = analyze(SQL_REPOSITORY + provider)

    assert any(
        item["code"] == "CAN001" and item["trace"][-1] == "api.SqlRepository.get"
        for item in diagnostics(report)
    )


def test_factory_nested_definitions_do_not_supply_results(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    provider = """
class AppProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self) -> Repository:
        def unrelated():
            return SqlRepository()
        class Unrelated:
            def create(self):
                yield SqlRepository()
        return unknown()
"""

    report = analyze(SQL_REPOSITORY + provider)

    [finding] = diagnostics(report)
    assert finding["code"] == "CAN003"
    assert not report["operations"][0]["complete"]


@pytest.mark.parametrize(
    "body",
    [
        "return SafeRepository()\nreturn SqlRepository()",
        "impl = SafeRepository()\nif self.flag:\n    impl = SqlRepository()\n    return SafeRepository()\nreturn impl",
        "def unrelated():\n    yield SqlRepository()\nreturn SafeRepository()",
    ],
    ids=["unreachable-return", "terminated-branch", "nested-yield"],
)
def test_factory_does_not_collect_unreturned_implementations(
    analyze: Callable[..., dict[str, Any]], body: str
) -> None:
    provider = (
        """
class SafeRepository(Repository):
    async def get(self) -> None:
        return None

class AppProvider(Provider):
    @provide(scope=Scope.REQUEST, provides=Repository)
    def repository(self):
"""
        + "\n".join("        " + line for line in body.splitlines())
        + "\n"
    )

    report = analyze(SQL_REPOSITORY + provider)

    assert diagnostics(report) == []
    assert report["operations"][0]["complete"]


@pytest.mark.parametrize(
    "body",
    [
        "if self.flag:\n    return SqlRepository()",
        "impl = SqlRepository()\nif self.flag:\n    impl = unknown()\nreturn impl",
        "impl = SqlRepository()\nwhile self.flag:\n    impl = unknown()\nreturn impl",
        "impl = SqlRepository()\nif (impl := unknown()):\n    pass\nreturn impl",
    ],
    ids=["fallthrough", "unknown-branch", "unmodeled-loop", "named-expression"],
)
def test_uncertain_factory_paths_prevent_complete_success(
    analyze: Callable[..., dict[str, Any]], body: str
) -> None:
    provider = (
        """
class AppProvider(Provider):
    @provide(scope=Scope.REQUEST, provides=Repository)
    def repository(self):
"""
        + "\n".join("        " + line for line in body.splitlines())
        + "\n"
    )

    report = analyze(SQL_REPOSITORY + provider, declaration="raises=[missing]")

    assert any(item["code"] == "CAN003" for item in diagnostics(report))
    assert not report["operations"][0]["complete"]
