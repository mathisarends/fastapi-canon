import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from abc import ABC, abstractmethod
from collections.abc import Iterator, AsyncIterator
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Error, Feature, Inject

class Missing(Exception): pass
class Conflict(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
conflict = Error(Conflict, status=409, code='conflict', title='Conflict')

class Repository(ABC):
    @abstractmethod
    async def get(self) -> None: ...

class LocalRepository(Repository):
    async def get(self) -> None:
        raise Missing()

router = APIRouter()
catalog = Feature(name='catalog', errors=[missing, conflict])
"""


@pytest.fixture
def scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., tuple[int, dict[str, Any]]]:
    def run(
        source: str, declaration: str = "raises=[missing]"
    ) -> tuple[int, dict[str, Any]]:
        (tmp_path / "api.py").write_text(
            dedent(PRELUDE + source)
            + f"\n@router.get('/', {declaration})\n"
            + "async def endpoint(repository: Inject[Repository]) -> None:\n"
            + "    await repository.get()\n",
            encoding="utf-8",
        )
        status = main(
            [
                "check",
                "errors",
                str(tmp_path),
                "--strict",
                "--require-complete",
                "--format",
                "json",
            ]
        )
        return status, json.loads(capsys.readouterr().out)

    return run


@pytest.mark.parametrize("conditional_cleanup", [False, True])
def test_conditional_resource_entries_preserve_branch_errors_and_exclude_shutdown(
    scan: Callable[..., tuple[int, dict[str, Any]]], conditional_cleanup: bool
) -> None:
    branch = (
        "try:\n                yield LocalRepository()\n            finally:\n                raise Conflict()"
        if conditional_cleanup
        else "yield ConflictingRepository()"
    )
    status, report = scan(
        f"""
class ConflictingRepository(Repository):
    async def get(self): raise Conflict()
class Dependencies(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self, enabled: bool) -> Iterator[Repository]:
        if enabled:
            {branch}
            return
        yield LocalRepository()
        raise Conflict()
""",
        declaration="",
    )
    [operation] = report["operations"]
    assert operation["complete"]
    assert report["contract_gaps"] == (1 if conditional_cleanup else 2)
    assert status == 1


def test_provider_results_refresh_when_a_later_route_changes_the_factory_input(
    scan: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    _, report = scan("""
class SafeRepository(Repository):
    async def get(self): pass
implementations = {'active': SafeRepository()}
class Dependencies(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self) -> Repository:
        return implementations['active']
@router.get('/reader')
async def reader(repository: Inject[Repository]): await repository.get()
@router.post('/change')
def change(): implementations['active'] = LocalRepository()
""")
    reader = next(o for o in report["operations"] if o["function"] == "api.reader")
    assert reader["complete"]
    assert [d["code"] for d in reader["diagnostics"]] == ["CAN001"]


@pytest.mark.parametrize(
    ("header", "body"),
    [
        ("def repository(self, local: LocalRepository) -> Repository:", "return local"),
        (
            "async def repository(self, local: LocalRepository) -> Repository:",
            "return local",
        ),
        (
            "def repository(self, local: LocalRepository) -> Iterator[Repository]:",
            "yield local",
        ),
        (
            "async def repository(self, local: LocalRepository) -> AsyncIterator[Repository]:",
            "yield local",
        ),
        (
            "def repository(self, local: 'LocalRepository') -> Repository:",
            "alias = local\nreturn alias",
        ),
        (
            "async def repository(self, local: LocalRepository) -> AsyncIterator[Repository]:",
            "try:\n    yield local\nfinally:\n    cleanup()",
        ),
    ],
    ids=[
        "sync",
        "async",
        "generator",
        "async-generator",
        "forward-reference-alias",
        "resource-cleanup",
    ],
)
@pytest.mark.parametrize("declared", [False, True], ids=["missing-contract", "covered"])
def test_registered_forwarded_dependencies_preserve_error_contracts(
    scan: Callable[..., tuple[int, dict[str, Any]]],
    header: str,
    body: str,
    declared: bool,
) -> None:
    source = (
        "\nclass AppProvider(Provider):\n"
        "    local = provide(LocalRepository, scope=Scope.REQUEST)\n"
        "    @provide(scope=Scope.REQUEST)\n"
        f"    {header}\n"
        + "\n".join("        " + line for line in body.splitlines())
        + "\n"
    )

    status, report = scan(source, "raises=[missing]" if declared else "")

    (operation,) = report["operations"]
    assert status == int(not declared)
    assert operation["complete"]
    assert report["incomplete_operations"] == 0
    assert [d["code"] for d in operation["diagnostics"]] == (
        [] if declared else ["CAN001"]
    )
    if not declared:
        assert operation["diagnostics"][0]["trace"][-1] == "api.LocalRepository.get"


def test_forwarded_abstract_dependencies_follow_all_registered_implementations(
    scan: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    source = """
class Intermediate(Repository): pass
class BridgeRepository(Intermediate):
    async def get(self) -> None:
        raise Missing()
class OtherRepository(BridgeRepository):
    async def get(self) -> None:
        raise Conflict()
class AppProvider(Provider):
    local = provide(BridgeRepository, scope=Scope.REQUEST)
    other = provide(OtherRepository, provides=BridgeRepository, scope=Scope.REQUEST, override=True)
    @provide(scope=Scope.REQUEST)
    def intermediate(self, local: BridgeRepository) -> Intermediate:
        return local
    @provide(scope=Scope.REQUEST)
    def repository(self, intermediate: Intermediate) -> Repository:
        return intermediate
"""

    status, report = scan(source)

    (operation,) = report["operations"]
    (finding,) = operation["diagnostics"]
    assert status == 1 and operation["complete"]
    assert finding["code"] == "CAN001"
    assert finding["trace"][-1] == "api.OtherRepository.get"


@pytest.mark.parametrize(
    "source",
    [
        """
class AppProvider(Provider, Repository):
    async def get(self) -> None:
        raise Missing()
    @provide(scope=Scope.REQUEST)
    def repository(self: 'AppProvider') -> Repository:
        return self
class OtherProvider(AppProvider):
    async def get(self) -> None:
        return None
class Registry(Provider):
    provider = provide(OtherProvider, provides=AppProvider, scope=Scope.REQUEST)
""",
        """
class AppProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self, local: LocalRepository) -> Repository:
        return local
""",
        """
class Intermediate(Repository): pass
class AppProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self, other: Intermediate) -> Repository:
        return other
    @provide(scope=Scope.REQUEST)
    def intermediate(self, repository: Repository) -> Intermediate:
        return repository
""",
        """
class AppProvider(Provider):
    local = provide(LocalRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    def repository(self, local: LocalRepository | None) -> Repository:
        return local
""",
        """
class AppProvider(Provider):
    local = provide(LocalRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    def repository(self, local: 'LocalRepository | None') -> Repository:
        return local
""",
        """
class AppProvider(Provider):
    local = provide(LocalRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    def repository(self, local: LocalRepository) -> Repository:
        if self.flag:
            local = unknown()
        return local
""",
        """
class AppProvider(Provider):
    local = provide(LocalRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    def repository(self, local: LocalRepository) -> Repository:
        local = unknown()
        return local
""",
    ],
    ids=[
        "bound-provider-instance",
        "unregistered",
        "cycle",
        "optional",
        "quoted-optional",
        "uncertain-branch",
        "reassignment",
    ],
)
def test_unresolved_forwarding_cannot_pass_the_complete_gate(
    scan: Callable[..., tuple[int, dict[str, Any]]], source: str
) -> None:
    status, report = scan(source)

    (operation,) = report["operations"]
    assert status == 1
    assert not operation["complete"]
    assert report["incomplete_operations"] == 1
    assert any(d["code"] == "CAN003" for d in operation["diagnostics"])


def test_forwarded_annotation_uncertainty_reaches_every_operation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bindings.py").write_text(
        "from api import LocalRepository\n"
        "if configured:\n    Implementation = LocalRepository\n"
        "else:\n    Implementation = LocalRepository\n",
        encoding="utf-8",
    )
    (tmp_path / "api.py").write_text(
        dedent(
            PRELUDE
            + """
import bindings
class AppProvider(Provider):
    local = provide(LocalRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    def repository(self, local: bindings.Implementation) -> Repository:
        return local

@router.get('/first', raises=[missing])
async def first(repository: Inject[Repository]) -> None:
    await repository.get()

@router.get('/second', raises=[missing])
async def second(repository: Inject[Repository]) -> None:
    await repository.get()
"""
        ),
        encoding="utf-8",
    )

    status = main(
        ["check", "errors", str(tmp_path), "--require-complete", "--format", "json"]
    )
    report = json.loads(capsys.readouterr().out)

    assert status == 1
    assert len(report["operations"]) == 2
    assert report["incomplete_operations"] == 2
    for operation in report["operations"]:
        assert any(
            finding["code"] == "CAN003" and finding["path"].endswith("bindings.py")
            for finding in operation["diagnostics"]
        )
