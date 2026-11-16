from fastapi import APIRouter

from fastapi_canon.error import ErrorRegistry
from fastapi_canon.error.contributions import error_registries
from fastapi_canon.exceptions import ErrorConfigurationError, FeatureConfigurationError
from fastapi_canon.routing.http import route_provenance
from fastapi_canon.routing.traversal import iter_contract_routes


def collect_router_errors(
    routers: tuple[APIRouter, ...], *, feature_name: str
) -> ErrorRegistry | None:
    registries: list[tuple[str, ErrorRegistry]] = []
    seen: set[int] = set()
    for router in routers:
        contributions = list(error_registries(router))
        for _, route in iter_contract_routes(router.routes, include_mounts=False):
            metadata = getattr(route, "openapi_extra", None)
            provenance = route_provenance(metadata)
            registry = provenance.error_registry if provenance is not None else None
            if registry is not None:
                contributions.append(registry)
            contributions.extend(error_registries(route.endpoint))
        for registry in contributions:
            if id(registry) in seen:
                continue
            seen.add(id(registry))
            registries.append((feature_name, registry))
    return merge_errors(
        tuple(registries),
        name=f"{feature_name}-routers" if len(registries) > 1 else None,
        type_base=None,
    )


def merge_errors(
    registries: tuple[tuple[str, ErrorRegistry], ...],
    *,
    name: str | None,
    type_base: str | None,
) -> ErrorRegistry | None:
    if not registries:
        return None
    distinct: list[tuple[str, ErrorRegistry]] = []
    seen: set[int] = set()
    for contribution in registries:
        if id(contribution[1]) in seen:
            continue
        seen.add(id(contribution[1]))
        distinct.append(contribution)
    registries = tuple(distinct)
    inferred_base = type_base
    registry_bases = {registry.type_base for _, registry in registries}
    if inferred_base is None and len(registry_bases) == 1:
        inferred_base = next(iter(registry_bases))
    try:
        return ErrorRegistry.merge(
            *(registry for _, registry in registries),
            name=name,
            type_base=inferred_base,
        )
    except ErrorConfigurationError as error:
        feature_names = ", ".join(repr(feature_name) for feature_name, _ in registries)
        msg = (
            f"error registries from features {feature_names} are incompatible: {error}"
        )
        raise FeatureConfigurationError(msg) from error
