from collections.abc import Callable
from typing import Any, cast

from jinja2 import Environment

from fastapi_canon.codegen.templating import template_environment


def environment() -> Environment:
    return template_environment(
        "fastapi_canon.integrations.sse.codegen", trim_blocks=True
    )


def add_filters(env: Environment, **filters: Callable[..., Any]) -> None:
    cast(dict[str, Callable[..., Any]], env.filters).update(filters)
