import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter, Error, Feature
class Missing(Exception): pass
missing = Error(Missing, status=404, code="missing", title="Missing")
feature = Feature(name="api", errors=[missing])
router = APIRouter()
def safe(): pass
def risky(): raise Missing()
"""


@pytest.fixture
def scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[[str], dict[str, Any]]:
    def run(source: str) -> dict[str, Any]:
        (tmp_path / "api.py").write_text(dedent(PRELUDE + source), encoding="utf8")
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
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        return report

    return run


@pytest.mark.parametrize("handler_use", ["callback()", "return callback"])
def test_exception_handlers_keep_values_assigned_before_failure(
    scan: Callable[[str], dict[str, Any]], handler_use: str
) -> None:
    report = scan(f"""
def choose():
    callback = safe
    try:
        callback = risky
        int('invalid')
        callback = safe
    except ValueError:
        {handler_use}
    return safe
@router.get('/handler')
def route(): choose()()
""")
    [operation] = report["operations"]
    assert any(d["code"] == "CAN001" for d in operation["diagnostics"])


def test_super_follows_the_own_parent_constructor(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
class Parent:
    def __init__(self): raise Missing()
class Child(Parent):
    def __init__(self): super().__init__()
@router.get('/super')
def route(): Child()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]
    assert operation["diagnostics"][0]["trace"][-1] == "api.Parent.__init__"


@pytest.mark.parametrize("translate", [False, True])
def test_library_errors_only_require_a_contract_when_translated_to_own_errors(
    scan: Callable[[str], dict[str, Any]], translate: bool
) -> None:
    report = scan(f"""
import sqlite3
def adapter():
    try:
        raise sqlite3.OperationalError('unavailable')
    except sqlite3.Error:
        {"raise Missing() from None" if translate else "raise"}
@router.get('/adapter')
def route(): adapter()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == (
        ["CAN001"] if translate else []
    )


def test_thread_call_follows_every_possible_bound_receiver(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
import asyncio
class Service:
    def __init__(self, callback): self.callback = callback
    def run(self): self.callback()
@router.get('/thread')
async def route(flag: bool):
    service = Service(safe) if flag else Service(risky)
    await asyncio.to_thread(service.run)
""")
    [operation] = report["operations"]

    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_constructor_callbacks_keep_distinct_call_contexts(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
class Service:
    def __init__(self, callback=safe): self.callback = callback
    def run(self): self.callback()
def make(callback=safe): return Service(callback)
@router.get("/safe")
def first(): make().run()
@router.get("/risky")
def second(): make(risky).run()
""")
    first, second = report["operations"]
    assert first["complete"] and first["diagnostics"] == []
    assert second["complete"]
    assert [d["code"] for d in second["diagnostics"]] == ["CAN001"]
    assert second["diagnostics"][0]["trace"][-1] == "api.risky"


def test_mutating_an_aliased_object_invalidates_the_effect_summary(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
class Service:
    def __init__(self): self.callback = safe
    def run(self): self.callback()
@router.get("/changed")
def route():
    service = Service()
    alias = service
    service.run()
    alias.callback = risky
    service.run()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


@pytest.mark.parametrize(
    "invocation",
    ["invoke(risky)", "invoke(callback=risky)", "invoke(**{'callback': risky})"],
)
def test_actual_keyword_and_unpacking_values_reach_callbacks(
    scan: Callable[[str], dict[str, Any]], invocation: str
) -> None:
    report = scan(f"""
def invoke(callback=safe): callback()
@router.get("/call")
def route(): {invocation}
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_explicit_implementation_is_not_replaced_by_a_provider(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
from abc import ABC, abstractmethod
from dishka import Provider, Scope, provide
class Port(ABC):
    @abstractmethod
    def run(self): ...
class Safe(Port):
    def run(self): pass
class Risky(Port):
    def run(self): raise Missing()
class Dependencies(Provider):
    scope = Scope.APP
    port = provide(Safe, provides=Port)
class Service:
    def __init__(self, port: Port): self.port: Port = port
    def run(self): self.port.run()
@router.get("/explicit")
def route(): Service(Risky()).run()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_dataclass_default_factory_is_part_of_construction(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
from dataclasses import dataclass, field
@dataclass
class Model:
    value: object = field(default_factory=risky)
@router.get("/model")
def route(): Model()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


@pytest.mark.parametrize(
    "invocation",
    [
        "pending = fail(); await pending",
        "pending = asyncio.create_task(fail()); await asyncio.shield(pending)",
        "await asyncio.to_thread(risky)",
    ],
)
def test_awaiting_stored_work_executes_its_actual_callback(
    scan: Callable[[str], dict[str, Any]], invocation: str
) -> None:
    report = scan(f"""
import asyncio
async def fail(): raise Missing()
@router.get("/async")
async def route():
    {invocation}
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_a_conditional_await_does_not_establish_completion(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
async def fail(): raise Missing()
@router.get("/async")
async def route(flag: bool):
    pending = fail()
    if flag:
        await pending
""")
    [operation] = report["operations"]
    assert not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN001", "CAN003"}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_generator_context_translates_body_errors_and_runs_cleanup(
    scan: Callable[[str], dict[str, Any]], asynchronous: bool
) -> None:
    prefix = "async " if asynchronous else ""
    report = scan(f"""
from contextlib import {"asynccontextmanager" if asynchronous else "contextmanager"} as manager
class Translated(Exception): pass
class Cleanup(Exception): pass
@manager
{prefix}def resource():
    try:
        yield "value"
    except Missing:
        raise Translated()
    finally:
        raise Cleanup()
@router.get("/context")
{prefix}def route():
    {prefix}with resource() as value:
        risky()
""")
    [operation] = report["operations"]
    assert any("api.Cleanup" in d["message"] for d in operation["diagnostics"])
    assert not any(d["code"] == "CAN001" for d in operation["diagnostics"])


def test_exit_stack_runs_registered_callback_at_close(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
from contextlib import ExitStack
@router.get("/stack")
def route():
    stack = ExitStack()
    stack.callback(risky)
    stack.close()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_exit_stack_cleanup_is_not_caught_by_its_entry_handler(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
from contextlib import ExitStack, contextmanager
@contextmanager
def resource():
    yield "ok"
    risky()
@router.get("/stack")
def route():
    stack = ExitStack()
    try:
        stack.enter_context(resource())
    except Missing:
        pass
    stack.close()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


@pytest.mark.parametrize("conditional", [False, True])
def test_exit_stack_suppression_requires_definite_registration(
    scan: Callable[[str], dict[str, Any]], conditional: bool
) -> None:
    registration = (
        "if flag: stack.enter_context(resource())"
        if conditional
        else "stack.enter_context(resource())"
    )
    report = scan(f"""
from contextlib import ExitStack, contextmanager
@contextmanager
def resource():
    try:
        yield "ok"
    except Missing:
        pass
@router.get("/stack")
def route(flag: bool):
    stack = ExitStack()
    {registration}
    stack.callback(risky)
    stack.close()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == (
        ["CAN001"] if conditional else []
    )


def test_scheduled_work_is_reported_separately_from_route_exceptions(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
import asyncio
async def background(): raise Missing()
@router.get("/schedule")
async def route(): asyncio.create_task(background())
""")
    [operation] = report["operations"]
    assert operation["complete"] and operation["diagnostics"] == []
    assert operation["scheduled_executions"] == ["api.background"]


@pytest.mark.parametrize("modified", [False, True])
def test_local_callback_captures_only_stable_bindings(
    scan: Callable[[str], dict[str, Any]], modified: bool
) -> None:
    report = scan(f"""
def run(callback):
    def local(): callback()
    {"callback = risky" if modified else "pass"}
    local()
@router.get("/closure")
def route(): run({"safe" if modified else "risky"})
""")
    [operation] = report["operations"]
    assert operation["complete"] == (not modified)
    assert {d["code"] for d in operation["diagnostics"]} == (
        {"CAN003"} if modified else {"CAN001"}
    )


def test_custom_allocation_does_not_claim_a_fixed_receiver(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
class Risky:
    def run(self): raise Missing()
class Factory:
    def __new__(cls): return Risky()
    def run(self): pass
@router.get("/factory")
def route(): Factory().run()
""")
    [operation] = report["operations"]
    assert not operation["complete"]
    assert any("allocation" in d["message"] for d in operation["diagnostics"])


def test_awaited_task_field_retains_its_source_coroutine(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
import asyncio
async def fail(): raise Missing()
class Worker:
    def __init__(self): self.task: asyncio.Task[None] | None = None
    def start(self): self.task = asyncio.create_task(fail())
    async def stop(self):
        task = self.task
        if task is not None: await task
@router.get("/worker")
async def route():
    worker = Worker()
    worker.start()
    await worker.stop()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_opaque_task_parameter_cannot_hide_awaited_application_errors(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
import asyncio
@router.get("/unknown")
async def route(task: asyncio.Task[None]): await task
""")
    [operation] = report["operations"]
    assert not operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN003"]


def test_recursive_summaries_share_heap_dependencies(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
class Service:
    def __init__(self): self.callback = safe
    def first(self, again: bool):
        if again: self.second()
        self.callback()
    def second(self): self.first(False)
@router.get("/recursive")
def route():
    service = Service()
    service.first(True)
    service.callback = risky
    service.second()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_recursion_does_not_hide_an_opaque_call(
    scan: Callable[[str], dict[str, Any]],
) -> None:
    report = scan("""
def recursive():
    recursive()
    unknown()
@router.get("/recursive")
def route(): recursive()
""")
    [operation] = report["operations"]
    assert not operation["complete"]
    assert any(
        d["message"] == "unresolved call: unknown" for d in operation["diagnostics"]
    )


@pytest.mark.parametrize("initial", ["safe", "risky"])
def test_local_defaults_are_evaluated_at_definition(
    scan: Callable[[str], dict[str, Any]], initial: str
) -> None:
    report = scan(f"""
@router.get("/defaults")
def route():
    callback = {initial}
    def local(value=callback): value()
    callback = {"risky" if initial == "safe" else "safe"}
    local()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == (
        [] if initial == "safe" else ["CAN001"]
    )
