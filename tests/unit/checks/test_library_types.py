import sys
from pathlib import Path

import pytest

from fastapi_canon.checks.library_types import (
    # Signature reading must be tested independently of the external-effect policy.
    _LibraryTypes,  # smelt: ignore[SMT420] -- signature-reader unit seam
)
from fastapi_canon.checks.value_flow import (
    # Input to the signature-reader unit seam, without executing a receiver.
    _Value,  # smelt: ignore[SMT420] -- symbolic receiver for signature-reader unit tests
)


@pytest.mark.parametrize(
    ("public", "internal"),
    [
        ("collections.abc.Mapping", "_collections_abc.Mapping"),
        ("typing.Generic", "_typing.Generic"),
        ("datetime.datetime", "_datetime.datetime"),
    ],
)
def test_standard_type_identity_is_independent_of_native_reexports(
    public: str, internal: str
) -> None:
    types = _LibraryTypes()
    assert types.canonical(public) == types.canonical(internal) == public
    assert types.presence("datetime.datetime", "isoformat") is True


def test_reexported_signatures_are_read_without_importing_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "signature_fixture"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise RuntimeError('must not import')\nfrom .api import Client\n",
        encoding="utf8",
    )
    (package / "api.py").write_text(
        """
from functools import cached_property
class Result:
    value: str
class Client:
    @cached_property
    def result(self) -> Result: ...
    async def fetch(self) -> Result: ...
""",
        encoding="utf8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    types = _LibraryTypes()
    receiver = _Value("signature_fixture.Client", instance=True)
    result = types.member(receiver, "result")
    assert result is not None and result.name == "signature_fixture.api.Result"
    callback = types.member(receiver, "fetch")
    assert callback is not None
    returned = types.returned(callback, awaited=True)
    assert returned is not None and returned.name == "signature_fixture.api.Result"
    assert "signature_fixture" not in sys.modules


def test_star_reexports_respect_all_without_leaking_imported_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "star_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("from .api import *\n", encoding="utf8")
    (package / "api.py").write_text(
        "import types\n__all__ = ['Client']\nclass Client: pass\n", encoding="utf8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    types = _LibraryTypes()

    assert types.canonical("star_fixture.Client") == "star_fixture.api.Client"
    assert types.canonical("star_fixture.types.Other") == "star_fixture.types.Other"
    assert "star_fixture" not in sys.modules


def test_unbound_library_type_variables_remain_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "generic_fixture.py").write_text(
        "from typing import TypeVar\nT = TypeVar('T')\nclass Client:\n    value: T\n",
        encoding="utf8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    result = _LibraryTypes().member(
        _Value("generic_fixture.Client", instance=True), "value"
    )

    assert result is not None and not result.name


def test_namespace_package_signatures_do_not_require_imported_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "signature_namespace" / "protocols"
    package.mkdir(parents=True)
    (package / "api.py").write_text(
        "raise RuntimeError('must not import')\nclass Client:\n    label: str\n",
        encoding="utf8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    types = _LibraryTypes()
    result = types.member(
        _Value("signature_namespace.protocols.api.Client", instance=True), "label"
    )
    assert result is not None and result.name == "builtins.str"
    assert "signature_namespace" not in sys.modules
