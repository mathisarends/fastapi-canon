import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize("scope", ["APP", "REQUEST"])
@pytest.mark.parametrize("resource", [False, True])
def test_service_scope_follows_registered_service_and_preserves_parent_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], scope: str, resource: bool
) -> None:
    factory = (
        "@provide\n    def service(self) -> Iterator[Service]: yield Service()"
        if resource
        else "service = provide(Service)"
    )
    (tmp_path / "api.py").write_text(
        f"""
from collections.abc import Iterator
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi_canon import APIRouter, Inject, ServiceScope
raise RuntimeError('source must not execute')
class Missing(Exception): pass
def safe(): pass
def fail(): raise Missing()
class Service:
    def __init__(self): self.callback = safe
    def change(self): self.callback = fail
    def run(self): self.callback()
class Services(Provider):
    scope = Scope.{scope}
    {factory}
router = APIRouter()
@router.get('/')
async def endpoint(container: Inject[AsyncContainer], service: Inject[Service]):
    service.change()
    work = ServiceScope(container, Service)
    async with work() as current:
        current.run()
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["code"] for d in operation["diagnostics"]] == (
        ["CAN002"] if scope == "APP" else []
    )
    if scope == "APP":
        assert operation["diagnostics"][0]["trace"][-1] == "api.fail"


@pytest.mark.parametrize(
    "boundary",
    [
        "component",
        "opaque_container",
        "request_cleanup",
        "aliased_request_cleanup",
        "app_cleanup",
        "unknown_scope",
        "provider_component",
        "uncached",
    ],
)
def test_service_scope_boundaries_remain_explicit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], boundary: str
) -> None:
    provider = "service = provide(Service)"
    scope = (
        ""
        if boundary == "unknown_scope"
        else "scope = Scope.APP"
        if boundary == "app_cleanup"
        else "scope = Scope.REQUEST"
    )
    if boundary in {"request_cleanup", "aliased_request_cleanup", "app_cleanup"}:
        provider = "@provide\n    def service(self) -> Iterator[Service]:\n        yield Service()\n        raise Missing()"
    if boundary == "aliased_request_cleanup":
        provider += "\n    service_alias = alias(Service, provides=Port)"
    if boundary == "provider_component":
        scope = "component = 'custom'"
        provider = "service = provide(Service, scope=Scope.REQUEST)"
    if boundary == "uncached":
        provider = "service = provide(Service, cache=False)"
    call = (
        "ServiceScope(container, Service, component='custom')"
        if boundary == "component"
        else "ServiceScope(container, Service)"
    )
    container = "object" if boundary == "opaque_container" else "AsyncContainer"
    if boundary == "aliased_request_cleanup":
        call = "ServiceScope(container, Port)"
    (tmp_path / "api.py").write_text(
        f"""
from collections.abc import Iterator
from dishka import AsyncContainer, Provider, Scope, alias, provide
from fastapi_canon import APIRouter, ServiceScope
class Missing(Exception): pass
class Port: pass
class Service(Port):
    def run(self): pass
class Services(Provider):
    {scope}
    {provider}
router = APIRouter()
@router.get('/')
async def endpoint(container: {container}):
    work = {call}
    async with work() as service:
        service.run()
""",
        encoding="utf8",
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    if boundary == "app_cleanup":
        assert status == 0 and operation["complete"] and operation["diagnostics"] == []
    else:
        assert status == 1 and not operation["complete"], operation["diagnostics"]
        assert any(d["code"] == "CAN003" for d in operation["diagnostics"])
    if boundary in {"request_cleanup", "aliased_request_cleanup"}:
        assert any("Dishka ExitError" in d["message"] for d in operation["diagnostics"])
        assert not any(d["code"] == "CAN002" for d in operation["diagnostics"])


def test_service_scope_checks_own_provider_entry_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        """
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi_canon import APIRouter, ServiceScope
class Missing(Exception): pass
class Service:
    def run(self): pass
class Services(Provider):
    scope = Scope.REQUEST
    @provide
    def service(self, flag: bool) -> Service:
        if flag: raise Missing()
        return Service()
router = APIRouter()
@router.get('/')
async def endpoint(container: AsyncContainer):
    async with ServiceScope(container, Service)() as service:
        service.run()
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["message"] for d in operation["diagnostics"]] == [
        "api.Missing: no Error mapping in raises or the application catalog"
    ]
