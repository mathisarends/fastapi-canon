import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

import fastapi_canon.cli as canon_cli

PRELUDE = """
import asyncio
from collections.abc import Mapping
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, SecretStr, field_validator

from fastapi_canon import APIRouter, Error, Feature, Inject

T = TypeVar("T")

class Missing(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"{key} missing")

missing = Error(Missing, status=404, code="missing", title="Missing")
router = APIRouter()
feature = Feature(name="api", routers=[router], errors=[missing])

def fail() -> None:
    raise Missing("x")

class Schema(BaseModel):
    name: str

class Session(Generic[T]):
    async def current(self) -> None:
        fail()

class Plain:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._token: SecretStr | None = None
        self._items: dict[str, int] = {}
        self._client = httpx.AsyncClient()

    def error(self) -> Missing:
        return Missing("x")
"""

ENDPOINT = """
@router.get("/")
async def endpoint(
    service: Inject[Plain], session: Inject[Session[str]], schema: Schema, name: str
) -> None:
{body}
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., dict[str, Any]]:
    def run(body: str, source: str = "") -> dict[str, Any]:
        endpoint = ENDPOINT.format(body=_indent(body))
        (tmp_path / "api.py").write_text(
            dedent(PRELUDE) + dedent(source) + endpoint, encoding="utf-8"
        )
        canon_cli.main(["check", "errors", str(tmp_path), "--format", "json"])
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        [operation] = report["operations"]
        result: dict[str, Any] = operation
        return result

    return run


def _indent(body: str) -> str:
    return "\n".join(f"    {line}" for line in dedent(body).strip().splitlines())


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("total = 0\ntotal += 1", id="syntax-augassign"),
        pytest.param("assert router is not None", id="syntax-assert"),
        pytest.param(
            "match schema.name:\n    case 'a':\n        pass\n    case _:\n        pass",
            id="syntax-match",
        ),
        pytest.param(
            "if (size := len(name)) > 1:\n    print(size)", id="syntax-walrus"
        ),
        pytest.param(
            "try:\n    ZoneInfo(name)\nexcept (ZoneInfoNotFoundError, ValueError):\n"
            "    pass",
            id="removal-library-except",
        ),
        pytest.param("async with service._lock:\n    pass", id="removal-library-with"),
        pytest.param("items = []\nitems.append(1)", id="names-local-list"),
        pytest.param("({'a': 1} | {'b': 2}).values()", id="names-binary-operation"),
        pytest.param("{'a': 'b'}['a'].upper()", id="names-subscript"),
        pytest.param("name.strip().lower()", id="names-str-parameter"),
        pytest.param("type(schema).__name__.lower()", id="names-type-builtin"),
        pytest.param("next(iter([1]))", id="names-next-builtin"),
        pytest.param("service._items.get('a', 0)", id="annotations-dict-field"),
        pytest.param(
            "service._token.get_secret_value()", id="annotations-optional-field"
        ),
        pytest.param(
            "response = await service._client.get('http://x')\n"
            "response.raise_for_status()\nresponse.json()",
            id="annotations-library-provenance",
        ),
        pytest.param("schema.model_dump()", id="annotations-library-model"),
        pytest.param("Schema.model_validate({'name': 'x'})", id="hooks-model-validate"),
        pytest.param("len(tuple(int(v) for v in ['1']))", id="deferred-genexp"),
        pytest.param("','.join(item for item in [name])", id="deferred-join"),
    ],
)
def test_ordinary_python_is_complete(
    analyze: Callable[..., dict[str, Any]], body: str
) -> None:
    operation = analyze(body)

    assert operation["diagnostics"] == []
    assert operation["complete"]


@pytest.mark.parametrize(
    ("body", "source", "frame"),
    [
        pytest.param("await session.current()", "", "api.fail", id="generic-inject"),
        pytest.param(
            "Child()",
            """
            class Base:
                def __init__(self) -> None:
                    fail()

            class Child(Base):
                def __init__(self) -> None:
                    super().__init__()
            """,
            "api.fail",
            id="super-init-chain",
        ),
        pytest.param("raise service.error()", "", "api.endpoint", id="raise-factory"),
        pytest.param(
            "Checked(name=name)",
            """
            class Checked(BaseModel):
                name: str

                @field_validator("name")
                @classmethod
                def check(cls, value: str) -> str:
                    fail()
                    return value
            """,
            "api.fail",
            id="validator",
        ),
        pytest.param(
            "tuple(lookup(item) for item in [name])",
            """
            def lookup(item: str) -> str:
                fail()
                return item
            """,
            "api.fail",
            id="generator-argument",
        ),
        pytest.param(
            "async with service._lock:\n    fail()", "", "api.fail", id="library-with"
        ),
        pytest.param(
            "try:\n    fail()\nexcept httpx.HTTPError:\n    pass",
            "",
            "api.fail",
            id="unrelated-library-except",
        ),
        pytest.param(
            "try:\n    ZoneInfo(name)\nexcept ValueError:\n    fail()",
            "",
            "api.fail",
            id="library-except-body",
        ),
        pytest.param(
            "walk(3)",
            """
            def walk(depth: int) -> None:
                if depth:
                    walk(depth - 1)
                fail()
            """,
            "api.fail",
            id="recursion",
        ),
    ],
)
def test_real_gaps_still_surface(
    analyze: Callable[..., dict[str, Any]], body: str, source: str, frame: str
) -> None:
    operation = analyze(body, source)

    [finding] = operation["diagnostics"]
    assert finding["code"] == "CAN001"
    assert finding["trace"][-1] == frame
    assert operation["complete"]


def test_library_handlers_catch_application_subclasses(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    source = """
    class Upstream(httpx.HTTPError):
        pass

    upstream = Error(Upstream, status=502, code="upstream", title="Upstream")

    def call() -> None:
        raise Upstream("x")
    """

    operation = analyze("try:\n    call()\nexcept httpx.HTTPError:\n    pass", source)

    assert operation["diagnostics"] == []
    assert operation["complete"]
