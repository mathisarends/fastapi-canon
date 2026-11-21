import inspect
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI


@contextmanager
def open_application(reference: str, *, root: Path) -> Iterator[FastAPI]:
    """Load an app and keep its project imports available to the caller."""
    with _application_path(root):
        value = _load_reference(reference)
        if callable(value) and not isinstance(value, FastAPI):
            value = _resolve_factory(value, reference)
        if not isinstance(value, FastAPI):
            raise TypeError(
                f"{reference} must resolve to a FastAPI application or a zero-argument factory returning one"
            )
        yield value


def load_reference(reference: str, *, root: Path) -> Any:
    with _application_path(root):
        return _load_reference(reference)


def _load_reference(reference: str) -> Any:
    module, separator, attribute = reference.partition(":")
    attributes = attribute.split(".")
    if (
        not separator
        or "/" in module
        or "\\" in module
        or not all(module.split("."))
        or not all(part.isidentifier() for part in attributes)
    ):
        raise ValueError(
            f"Invalid target {reference!r}; use an explicit module:attribute target with optional dotted attributes"
        )
    try:
        value: Any = import_module(module)
    except (Exception, SystemExit) as error:
        raise ValueError(
            f"Cannot import {reference!r} ({type(error).__name__}); check the module path and move runtime configuration into lifespan or runtime dependencies"
        ) from None
    try:
        for part in attributes:
            value = getattr(value, part)
    except (Exception, SystemExit) as error:
        raise ValueError(
            f"Cannot resolve attribute in {reference!r} ({type(error).__name__}); check each dotted attribute"
        ) from None
    return value


def _resolve_factory(value: Any, reference: str) -> Any:
    call = inspect.getattr_static(type(value), "__call__", None)
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(call):
        raise TypeError(f"{reference!r} must use a synchronous app factory")
    if (
        inspect.isgeneratorfunction(value)
        or inspect.isasyncgenfunction(value)
        or inspect.isgeneratorfunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise TypeError(f"{reference!r} cannot use a generator factory")
    try:
        inspect.signature(value).bind()
    except (Exception, SystemExit):
        raise TypeError(
            f"{reference!r} must be an inspectable zero-argument factory"
        ) from None
    try:
        result = value()
    except (Exception, SystemExit) as error:
        raise ValueError(
            f"Cannot call factory {reference!r} ({type(error).__name__}); move runtime configuration into lifespan or runtime dependencies"
        ) from None
    if inspect.isawaitable(result) or inspect.isasyncgen(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError(f"{reference!r} must use a synchronous app factory")
    if inspect.isgenerator(result):
        result.close()
        raise TypeError(f"{reference!r} cannot return a generator from its factory")
    return result


@contextmanager
def _application_path(root: Path) -> Iterator[None]:
    previous = sys.path[:]
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path[:] = previous
