"""Client generators registered through the ``fastapi_canon.generators`` entry points.

A distribution registers a generator under its target name, for example::

    [project.entry-points."fastapi_canon.generators"]
    graphql = "my_package.codegen:GENERATOR"

The referenced object implements ``Generator``. Keep its module light: import
rendering dependencies inside ``render`` so configuration loading stays cheap.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Literal, Protocol

__all__ = [
    "GROUP",
    "Generator",
    "Rendering",
    "TargetConfig",
    "contract_kinds",
    "load_generator",
]

GROUP = "fastapi_canon.generators"


@dataclass(frozen=True)
class TargetConfig:
    """One validated ``[[targets]]`` entry of the code generation configuration."""

    name: str
    generator: str
    """The registered generator name, or ``external``."""
    language: str = "typescript"
    output: Path | None = None
    package: str = "api_client"
    client_name: str = "ApiClient"
    http_client: Literal["httpx", "httpx2"] = "httpx"
    generate_command: tuple[str, ...] = ()
    check_command: tuple[str, ...] = ()
    timeout: int = 120
    strict: bool = False
    diagnostics: Path | None = None
    allow_diagnostics: Path | None = None
    include_operations: tuple[str, ...] | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    """Generator-specific settings that Canon does not interpret."""


@dataclass(frozen=True, slots=True)
class Rendering:
    """Rendered client files and the writer that owns their output directory.

    ``write(check=...)`` returns the paths that differ; with ``check=True`` it
    must not write.
    """

    files: Mapping[str, str]
    write: Callable[..., tuple[Path, ...]]


class Generator(Protocol):
    """Render a client for one target from one exported contract document."""

    @property
    def contract(self) -> str:
        """The contract kind this generator reads, such as ``openapi``."""
        ...

    @property
    def settings(self) -> Mapping[str, frozenset[str]]:
        """Accepted target keys per supported language.

        The first language is the default unless ``typescript`` is supported.
        ``generator``, ``language`` and ``output`` are always accepted.
        """
        ...

    def render(
        self, target: "TargetConfig", document: dict[str, Any], contracts: Path
    ) -> Rendering:
        """Render *target* from *document*; *contracts* is the contract directory."""
        ...


def contract_kinds() -> frozenset[str]:
    """Return ``openapi`` and every contract kind read by an installed generator."""
    kinds = {"openapi"}
    for name in _registered():
        try:
            kinds.add(load_generator(name).contract)
        except (ImportError, TypeError):
            continue
    return frozenset(kinds)


def load_generator(name: str) -> Generator:
    """Load the generator registered as *name*."""
    registered = _registered()
    if name not in registered:
        available = ", ".join(sorted(registered)) or "none"
        raise ValueError(
            f"Unknown generator {name!r}; installed generators: {available}. "
            "Install the extra or package that provides it."
        )
    generator: Generator = registered[name].load()
    if not isinstance(getattr(generator, "contract", None), str) or not callable(
        getattr(generator, "render", None)
    ):
        raise TypeError(f"Generator {name!r} does not implement Generator")
    return generator


@cache
def _registered() -> dict[str, Any]:
    return {point.name: point for point in entry_points(group=GROUP)}
