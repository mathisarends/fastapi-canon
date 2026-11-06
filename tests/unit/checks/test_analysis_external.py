import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter, Error
from fastapi_canon.error import ErrorRegistry

class Missing(Exception):
    pass

missing = Error(Missing, status=404, code="missing", title="Missing")
router = APIRouter(error_registry=ErrorRegistry(errors=[missing]))

def fail() -> None:
    raise Missing()
"""

ENDPOINT = """
@router.get('/')
async def endpoint() -> None:
{body}
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., dict[str, dict[str, Any]]]:
    def run(
        body: str, source: str = "", root: Path | None = None
    ) -> dict[str, dict[str, Any]]:
        directory = root or tmp_path / "service"
        directory.mkdir(parents=True, exist_ok=True)
        endpoint = ENDPOINT.format(body=_indent(body))
        (directory / "api.py").write_text(
            dedent(PRELUDE) + dedent(source) + endpoint, encoding="utf-8"
        )
        main(["check", "errors", str(directory), "--format", "json"])
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        operations = {item["function"]: item for item in report["operations"]}
        return operations

    return run


def _indent(body: str) -> str:
    return "\n".join(f"    {line}" for line in dedent(body).strip().splitlines())


def _messages(operation: dict[str, Any]) -> list[tuple[str, str]]:
    return [(item["code"], item["message"]) for item in operation["diagnostics"]]


@pytest.mark.parametrize(
    "body",
    [
        "compare_digest(b'a', b'b')",
        "json.dumps({'a': 1}, indent=2).encode()",
        "datetime.now(UTC).isoformat()",
        "','.join(['a', f'{1}']).upper()",
        "len([1, 2])",
        "Response(id=1)",
        "Stamp(value=1)",
    ],
)
def test_library_calls_raise_no_application_errors(
    analyze: Callable[..., dict[str, dict[str, Any]]], body: str
) -> None:
    source = """
    import json
    from dataclasses import dataclass
    from datetime import UTC, datetime
    from hmac import compare_digest
    from pydantic import BaseModel

    class Response(BaseModel):
        id: int

    @dataclass(frozen=True)
    class Stamp:
        value: int
    """

    operation = analyze(body, source)["api.endpoint"]

    assert _messages(operation) == []
    assert operation["complete"]


def test_typed_library_receivers_and_fields_are_external(
    analyze: Callable[..., dict[str, dict[str, Any]]],
) -> None:
    source = """
    from pydantic import BaseModel, SecretStr

    class Settings(BaseModel):
        token: SecretStr

    class Verifier:
        def __init__(self, settings: Settings) -> None:
            self._settings = settings

        def verify(self) -> str:
            return self._settings.token.get_secret_value().strip()

    def check(verifier: Verifier) -> str:
        return verifier.verify()
    """

    operation = analyze("check(Verifier(Settings(token='x')))", source)["api.endpoint"]

    assert operation["complete"]


def test_attributes_assigned_by_base_constructors_keep_their_type(
    analyze: Callable[..., dict[str, dict[str, Any]]],
) -> None:
    source = """
    from abc import ABC
    from pydantic import SecretStr

    class Base(ABC):
        def __init__(self, token: SecretStr) -> None:
            self._token = token

    class Verifier(Base):
        def __init__(self, token: SecretStr) -> None:
            super().__init__(token)

        def verify(self) -> str:
            return self._token.get_secret_value()

    def check(verifier: Verifier) -> str:
        return verifier.verify()
    """

    operation = analyze("check(Verifier(SecretStr('x')))", source)["api.endpoint"]

    assert ("CAN003", "unresolved call: self._token.get_secret_value") not in (
        _messages(operation)
    )


@pytest.mark.parametrize(
    "body",
    [
        "sorted([3, 1], key=lambda item: fail())",
        "list(map(helper, [1]))",
        "Holder(value=1)",
    ],
    ids=["lambda", "function", "post-init"],
)
def test_application_code_called_by_libraries_is_followed(
    analyze: Callable[..., dict[str, dict[str, Any]]], body: str
) -> None:
    source = """
    from pydantic import BaseModel

    def helper(value: int) -> int:
        fail()
        return value

    class Holder(BaseModel):
        value: int

        def model_post_init(self, context: object) -> None:
            fail()
    """

    operation = analyze(body, source)["api.endpoint"]

    [finding] = operation["diagnostics"]
    assert finding["code"] == "CAN001"
    assert finding["trace"][-1] == "api.fail"


def test_decorated_hooks_of_library_bases_stay_incomplete(
    analyze: Callable[..., dict[str, dict[str, Any]]],
) -> None:
    source = """
    from pydantic import BaseModel, field_validator

    class Request(BaseModel):
        title: str

        @field_validator("title")
        @classmethod
        def strip(cls, value: str) -> str:
            return value.strip()
    """

    operation = analyze("Request(title='x')", source)["api.endpoint"]

    assert _messages(operation) == [
        (
            "CAN003",
            "library construction may call decorated hook: api.Request.strip",
        )
    ]


def test_library_dependencies_are_external(
    analyze: Callable[..., dict[str, dict[str, Any]]],
) -> None:
    source = """
    from typing import Annotated
    from fastapi import Depends
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    bearer = HTTPBearer(auto_error=False)

    @router.get('/me')
    async def me(
        credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    ) -> str:
        return credentials.scheme.lower()
    """

    operations = analyze("return None", source)

    assert operations["api.me"]["complete"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("missing_package.run()", "unresolved call: missing_package.run"),
        ("getattr(Service(), 'run')()", "unresolved call: getattr(Service(), 'run')"),
        ("json.loads('{}').run()", "unresolved call: json.loads('{}').run"),
    ],
    ids=["not-importable", "dynamic-builtin", "application-method-on-result"],
)
def test_unknown_or_dynamic_targets_stay_incomplete(
    analyze: Callable[..., dict[str, dict[str, Any]]], body: str, message: str
) -> None:
    source = """
    import json
    import missing_package

    class Service:
        def run(self) -> None:
            fail()
    """

    operation = analyze(body, source)["api.endpoint"]

    assert ("CAN003", message) in _messages(operation)
    assert not operation["complete"]


def test_packages_overlapping_a_scanned_root_are_not_external(
    analyze: Callable[..., dict[str, dict[str, Any]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "site" / "shop"
    (package / "billing").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "billing" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    source = """
    from shop.orders import place
    """

    operation = analyze("place()", source, root=package / "billing")["api.endpoint"]

    assert _messages(operation) == [("CAN003", "unresolved call: place")]
