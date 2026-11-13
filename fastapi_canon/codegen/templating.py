from functools import lru_cache

try:
    from jinja2 import Environment, PackageLoader, StrictUndefined
except ModuleNotFoundError as error:
    raise ModuleNotFoundError(
        "Client generation requires the optional codegen dependencies; "
        "install fastapi-canon[codegen]",
        name="jinja2",
    ) from error


def template_environment(package: str, *, trim_blocks: bool = False) -> Environment:
    """Return an isolated overlay so target filters cannot leak across renders."""
    environment = _environment(package, trim_blocks).overlay()
    environment.filters = environment.filters.copy()
    return environment


@lru_cache
def _environment(package: str, trim_blocks: bool) -> Environment:
    return Environment(
        loader=PackageLoader(package, "templates"),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=trim_blocks,
        lstrip_blocks=trim_blocks,
    )
