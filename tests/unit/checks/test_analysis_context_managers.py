import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from collections.abc import AsyncIterator
from fastapi_canon import APIRouter, Error, Feature
raise RuntimeError('The source module must never be imported')

class Missing(Exception): pass
class EnterFailure(Exception): pass
class ExitFailure(Exception): pass

def require_item(item_id: int) -> int:
    if item_id < 0: raise Missing()
    return item_id

router = APIRouter()
"""


@pytest.mark.parametrize("manager", ["asyncio.Lock()", "asyncio.timeout(1)"])
@pytest.mark.parametrize("mapping", ["registered", "declared"])
def test_asyncio_managers_preserve_body_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]], manager: str, mapping: str
) -> None:
    status, report = analyze(
        f"""
import asyncio
@router.get('/')
async def endpoint():
    async with {manager}:
        require_item(-1)
""",
        mapping=mapping,
    )
    (operation,) = report["operations"]
    assert operation["complete"]
    assert status == int(mapping != "declared")
    assert [d["code"] for d in operation["diagnostics"]] == (
        [] if mapping == "declared" else ["CAN001"]
    )


@pytest.mark.parametrize(
    "owner,method,awaited,arguments",
    [
        ("ExitStack", "close", "", ""),
        ("AsyncExitStack", "aclose", "await ", ""),
        ("ExitStack", "__exit__", "", "None, None, None"),
        ("AsyncExitStack", "__aexit__", "await ", "None, None, None"),
        ("ExitStack", "pop_all", "", ""),
    ],
)
def test_stored_exit_stack_cannot_hide_application_cleanup(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
    owner: str,
    method: str,
    awaited: str,
    arguments: str,
) -> None:
    status, report = analyze(f"""
from contextlib import {owner}
class Resource:
    stack: {owner}
@router.get('/')
async def endpoint(resource: Resource):
    {awaited}resource.stack.{method}({arguments})
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any("resource ownership" in d["message"] for d in operation["diagnostics"])


def test_implicit_builtin_reraise_preserves_caught_application_subclasses(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class StorageFailure(OSError): pass
def save():
    raise StorageFailure()
@router.get('/')
def endpoint():
    try:
        save()
    except OSError:
        raise
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"]
    assert "api.StorageFailure" in operation["diagnostics"][0]["message"]


@pytest.mark.parametrize(
    "imports,manager",
    [
        ("import contextlib", "contextlib.suppress"),
        ("import contextlib as contexts", "contexts.suppress"),
        ("from contextlib import suppress as ignore", "ignore"),
    ],
)
def test_contextlib_suppress_catches_subclasses_and_multiple_types(
    analyze: Callable[..., tuple[int, dict[str, Any]]], imports: str, manager: str
) -> None:
    status, report = analyze(f"""
{imports}
class SpecificMissing(Missing): pass
@router.get('/')
def endpoint(flag: bool):
    with {manager}(Missing, EnterFailure) as ignored:
        if flag: raise SpecificMissing()
        raise EnterFailure()
""")
    assert status == 0 and report["operations"][0]["complete"]


@pytest.mark.parametrize("types", ["", "ExitFailure"])
def test_contextlib_suppress_keeps_unmatched_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]], types: str
) -> None:
    status, report = analyze(
        f"""
from contextlib import suppress
@router.get('/')
def endpoint():
    with suppress({types}): require_item(-1)
""",
        mapping="registered",
    )
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


@pytest.mark.parametrize("types", ["error_type", "*types", "Missing, error_type"])
def test_dynamic_suppress_types_remain_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]], types: str
) -> None:
    status, report = analyze(f"""
from contextlib import suppress
@router.get('/')
def endpoint(error_type, types):
    with suppress({types}): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN002", "CAN003"}


def test_suppress_does_not_catch_argument_evaluation_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
from contextlib import suppress
def exception_type(): raise Missing()
@router.get('/')
def endpoint():
    with suppress(exception_type()): pass
""")
    assert status == 1
    assert any(
        d["code"] == "CAN002" and d["trace"] == ["api.endpoint", "api.exception_type"]
        for d in report["operations"][0]["diagnostics"]
    )


def test_suppress_keeps_outer_exit_failures_and_inner_unknowns(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
from contextlib import suppress
class Outer:
    def __enter__(self): return self
    def __exit__(self, *args): raise ExitFailure()
@router.get('/')
def endpoint(service):
    with Outer(), suppress(Missing):
        service.unknown()
        require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert [
        d["message"].split(":", 1)[0]
        for d in operation["diagnostics"]
        if d["code"] == "CAN002"
    ] == ["api.ExitFailure"]


@pytest.fixture
def analyze(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Callable[..., tuple[int, dict[str, Any]]]:
    def run(body: str, *, mapping: str = "none") -> tuple[int, dict[str, Any]]:
        prelude = PRELUDE
        if mapping != "none":
            prelude = prelude.replace(
                "router = APIRouter()",
                "missing = Error(Missing, status=404, code='missing', title='Missing')\n"
                "router = APIRouter()\n"
                "feature = Feature(name='api', routers=[router], errors=[missing])",
            )
        if mapping == "declared":
            body = body.replace(
                "@router.get('/')", "@router.get('/', raises=[missing])"
            )
        (tmp_path / "api.py").write_text(
            dedent(prelude) + dedent(body), encoding="utf-8"
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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("exit_body", ["pass", "return", "return None", "return False"])
@pytest.mark.parametrize("mapping", ["none", "registered", "declared"])
def test_local_nonsuppressing_manager_follows_body(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
    asynchronous: bool,
    exit_body: str,
    mapping: str,
) -> None:
    prefix = "async " if asynchronous else ""
    enter = "__aenter__" if asynchronous else "__enter__"
    exit_method = "__aexit__" if asynchronous else "__exit__"
    status, report = analyze(
        f"""
class Manager:
    {prefix}def {enter}(self): return self
    {prefix}def {exit_method}(self, exc_type, exc, tb): {exit_body}
@router.get('/')
{prefix}def endpoint():
    {prefix}with Manager():
        require_item(-1)
""",
        mapping=mapping,
    )
    (operation,) = report["operations"]
    assert operation["complete"]
    assert status == (0 if mapping == "declared" else 1)
    assert [d["code"] for d in operation["diagnostics"]] == (
        []
        if mapping == "declared"
        else ["CAN001" if mapping == "registered" else "CAN002"]
    )
    if mapping != "declared":
        assert operation["diagnostics"][0]["trace"] == [
            "api.endpoint",
            "api.require_item",
        ]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_enter_and_exit_helpers_keep_protocol_call_paths(
    analyze: Callable[..., tuple[int, dict[str, Any]]], asynchronous: bool
) -> None:
    prefix = "async " if asynchronous else ""
    enter = "__aenter__" if asynchronous else "__enter__"
    exit_method = "__aexit__" if asynchronous else "__exit__"
    status, report = analyze(f"""
def entering(flag: bool):
    if flag: raise EnterFailure()
def leaving(flag: bool):
    if flag: raise ExitFailure()
class Manager:
    {prefix}def {enter}(self):
        entering(True)
        return self
    {prefix}def {exit_method}(self, exc_type, exc, tb):
        leaving(True)
        return False
@router.get('/')
{prefix}def endpoint():
    {prefix}with Manager(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert {tuple(d["trace"]) for d in operation["diagnostics"]} == {
        ("api.endpoint", "api.Manager." + enter, "api.entering"),
        ("api.endpoint", "api.Manager." + exit_method, "api.leaving"),
        ("api.endpoint", "api.require_item"),
    }


@pytest.mark.parametrize("stage", ["enter", "exit"])
def test_unconditional_protocol_raise_has_no_spurious_body_escape(
    analyze: Callable[..., tuple[int, dict[str, Any]]], stage: str
) -> None:
    status, report = analyze(f"""
class Manager:
    def __enter__(self): {"raise EnterFailure()" if stage == "enter" else "return self"}
    def __exit__(self, exc_type, exc, tb): raise ExitFailure()
@router.get('/')
def endpoint():
    with Manager(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["message"].split(":", 1)[0] for d in operation["diagnostics"]] == [
        "api." + ("EnterFailure" if stage == "enter" else "ExitFailure")
    ]


@pytest.mark.parametrize(
    "exit_body",
    [
        "return flag",
        "return bool(exc)",
        "if exc is None: return False\n        return True",
    ],
)
def test_unknown_or_conditional_suppression_is_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]], exit_body: str
) -> None:
    status, report = analyze(f"""
class Manager:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        {exit_body}
@router.get('/')
def endpoint():
    with Manager(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any(
        d["code"] == "CAN003" and "suppression" in d["message"]
        for d in operation["diagnostics"]
    )
    assert any(
        d["message"].split(":", 1)[0] == "api.Missing" for d in operation["diagnostics"]
    )


@pytest.mark.parametrize("body", ["pass", "return 42"])
def test_normal_completion_and_literal_suppression(
    analyze: Callable[..., tuple[int, dict[str, Any]]], body: str
) -> None:
    status, report = analyze(f"""
class Manager:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return True
@router.get('/')
def endpoint():
    with Manager():
        {body}
""")
    assert status == 0 and report["operations"][0]["complete"]


def test_inner_suppression_keeps_outer_exit_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Outer:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): raise ExitFailure()
class Inner:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return True
@router.get('/')
def endpoint():
    with Outer(), Inner(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["message"].split(":", 1)[0] for d in operation["diagnostics"]] == [
        "api.ExitFailure"
    ]


def test_outer_suppression_catches_inner_enter_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Outer:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return True
class Inner:
    def __enter__(self): raise EnterFailure()
    def __exit__(self, exc_type, exc, tb): raise ExitFailure()
@router.get('/')
def endpoint():
    with Outer(), Inner(): require_item(-1)
""")
    assert status == 0 and report["operations"][0]["complete"]


def test_enclosing_handler_catches_protocol_and_body_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Manager:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        if exc: raise ExitFailure()
        return False
@router.get('/')
def endpoint():
    try:
        with Manager(): require_item(-1)
    except (Missing, ExitFailure): pass
""")
    assert status == 0 and report["operations"][0]["complete"]


@pytest.mark.parametrize(
    "expression,definition",
    [
        ("external()", "from contextlib import nullcontext as external"),
        ("Manager()", "class Manager: pass"),
        (
            "Manager()",
            "class Manager:\n    async def __enter__(self): return self\n    def __exit__(self, *args): return False",
        ),
        (
            "Manager()",
            "class Manager:\n    def __enter__(self): yield self\n    def __exit__(self, *args): return False",
        ),
        (
            "Manager",
            "class Manager:\n    def __enter__(self): return self\n    def __exit__(self, *args): return False",
        ),
        (
            "make()",
            "class Manager:\n    def __enter__(self): return self\n    def __exit__(self, *args): return False\nasync def make() -> Manager: return Manager()",
        ),
    ],
)
def test_unknown_or_invalid_protocol_keeps_body_and_can003(
    analyze: Callable[..., tuple[int, dict[str, Any]]], expression: str, definition: str
) -> None:
    status, report = analyze(f"""
{definition}
@router.get('/')
def endpoint():
    with {expression}: require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any(
        d["code"] == "CAN003" and "context manager protocol" in d["message"]
        for d in operation["diagnostics"]
    )
    assert any(
        d["message"].split(":", 1)[0] == "api.Missing" for d in operation["diagnostics"]
    )


@pytest.mark.parametrize(
    "binding", ["direct", "local", "parameter", "factory", "async_factory"]
)
def test_resolved_manager_and_enter_return_bindings(
    analyze: Callable[..., tuple[int, dict[str, Any]]], binding: str
) -> None:
    creation = {
        "direct": "Manager()",
        "local": "manager",
        "parameter": "manager",
        "factory": "make()",
        "async_factory": "await make_async()",
    }[binding]
    setup = "manager = Manager()" if binding == "local" else "pass"
    parameters = "manager: Manager" if binding == "parameter" else ""
    status, report = analyze(f"""
from typing import Self
class Manager:
    def __enter__(self) -> Self: return self
    def __exit__(self, *args): return False
    def require(self): raise Missing()
def make() -> Manager: return Manager()
async def make_async() -> Manager: return Manager()
@router.get('/')
async def endpoint({parameters}):
    {setup}
    with {creation} as entered: entered.require()
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["trace"] for d in operation["diagnostics"]] == [
        ["api.endpoint", "api.Manager.require"]
    ]


def test_literal_true_suppresses_only_body_not_its_own_exit(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Manager:
    def __enter__(self): return self
    def __exit__(self, *args):
        if args: raise ExitFailure()
        return True
@router.get('/')
def endpoint():
    with Manager(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["message"].split(":", 1)[0] for d in operation["diagnostics"]] == [
        "api.ExitFailure"
    ]


@pytest.mark.parametrize("modification", ["inherited", "decorated", "async_generator"])
def test_unsupported_protocol_definitions_remain_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]], modification: str
) -> None:
    definition = {
        "inherited": "class Base:\n    def __enter__(self): return self\n    def __exit__(self, *args): return False\nclass Manager(Base): pass",
        "decorated": "def decorate(method): return method\nclass Manager:\n    @decorate\n    def __enter__(self): return self\n    def __exit__(self, *args): return False",
        "async_generator": "class Manager:\n    async def __aenter__(self): yield self\n    async def __aexit__(self, *args): return False",
    }[modification]
    prefix = "async " if modification == "async_generator" else ""
    status, report = analyze(f"""
{definition}
@router.get('/')
async def endpoint():
    {prefix}with Manager(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any(d["code"] == "CAN003" for d in operation["diagnostics"])


def test_constructor_and_context_expression_errors_have_traces(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Manager:
    def __init__(self, flag):
        if flag: raise EnterFailure()
    def __enter__(self): return self
    def __exit__(self, *args): return False
def make(flag) -> Manager:
    if flag: raise ExitFailure()
    return Manager(flag)
@router.get('/')
def endpoint(flag: bool):
    with make(flag): pass
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert {tuple(d["trace"]) for d in operation["diagnostics"]} == {
        ("api.endpoint", "api.make"),
        ("api.endpoint", "api.make", "api.Manager.__init__"),
    }


def test_unknown_destructured_enter_binding_does_not_claim_completeness(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Manager:
    def __enter__(self): return (1, 2)
    def __exit__(self, *args): return False
@router.get('/')
def endpoint():
    with Manager() as (first, second): require_item(first)
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any("binding target" in d["message"] for d in operation["diagnostics"])


def test_conditional_enter_return_does_not_hide_body_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Manager:
    def __enter__(self):
        if self: return self
        raise EnterFailure()
    def __exit__(self, *args): return False
@router.get('/')
def endpoint():
    with Manager(): require_item(-1)
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert {d["message"].split(":", 1)[0] for d in operation["diagnostics"]} == {
        "api.Missing",
        "api.EnterFailure",
    }
