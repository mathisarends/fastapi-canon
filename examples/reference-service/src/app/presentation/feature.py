from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dishka import Provider
from fastapi import APIRouter, FastAPI


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    routers: Sequence[APIRouter] = ()
    providers: Sequence[type[Provider]] = ()
    register_exception_handlers: Callable[[FastAPI], None] | None = None
