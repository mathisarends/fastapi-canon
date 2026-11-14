from typing import Any

from fastapi_canon.codegen.templating import template_environment


def render_template(
    name: str,
    *,
    filters: dict[str, Any] | None = None,
    **context: Any,
) -> str:
    environment = template_environment("fastapi_canon.integrations.websockets.codegen")
    if filters:
        environment = environment.overlay()
        environment.filters.update(filters)
    return environment.get_template(name).render(**context)
