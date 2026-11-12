import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from collections.abc import AsyncIterator
from fastapi_canon import APIRouter, Error, Feature
raise RuntimeError('The source module must never be imported')

class Missing(Exception): pass
class IterationFailure(Exception): pass
class FilterFailure(Exception): pass

def require_item(item_id: int) -> int:
    if item_id < 0: raise Missing()
    return item_id

router = APIRouter()
"""


@pytest.fixture
def analyze(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Callable[..., tuple[int, dict[str, Any]]]:
    def run(body: str, *, mapping: str = "none") -> tuple[int, dict[str, Any]]:
        prelude = PRELUDE
        if mapping != "none":
            prelude = prelude.replace(
                "router = APIRouter()",
                "missing = Error(Missing, status=404, code='missing', title='Missing')\n"
                "router = APIRouter()\n"
                "feature = Feature(name='api', routers=[router], errors=[missing])",
            )
        if mapping == "declared":
            body = body.replace(
                "@router.get('/')", "@router.get('/', raises=[missing])"
            )
        (tmp_path / "api.py").write_text(
            dedent(prelude) + dedent(body), encoding="utf-8"
        )
        status = main(
            [
                "check",
                "errors",
                str(tmp_path),
                "--strict",
                "--require-complete",
                "--format",
                "json",
            ]
        )
        return status, json.loads(capsys.readouterr().out)

    return run


@pytest.mark.parametrize(
    "expression",
    [
        "[require_item(item) for item in item_ids]",
        "{require_item(item) for item in item_ids}",
        "{item: require_item(item) for item in item_ids}",
        "{require_item(item): item for item in item_ids}",
    ],
)
@pytest.mark.parametrize("mapping", ["none", "registered", "declared"])
def test_eager_forms_keep_mapping_and_declaration_diagnostics(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
    expression: str,
    mapping: str,
) -> None:
    status, report = analyze(
        f"""
@router.get('/')
def endpoint(item_ids: list[int]):
    return {expression}
""",
        mapping=mapping,
    )
    (operation,) = report["operations"]
    assert operation["complete"]
    if mapping == "declared":
        assert status == 0 and operation["diagnostics"] == []
    else:
        assert status == 1
        (diagnostic,) = operation["diagnostics"]
        assert diagnostic["code"] == ("CAN001" if mapping == "registered" else "CAN002")
        assert diagnostic["trace"] == ["api.endpoint", "api.require_item"]
        assert diagnostic["path"].endswith("api.py")


@pytest.mark.parametrize("container", ["[Item()]", "(Item(),)"])
def test_reversed_native_sequences_keep_application_element_methods(
    analyze: Callable[..., tuple[int, dict[str, Any]]], container: str
) -> None:
    _, report = analyze(f"""
class Item:
    def require(self): raise Missing()
@router.get('/')
def endpoint(): return [item.require() for item in reversed({container})]
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"]
    assert operation["diagnostics"][0]["trace"][-1] == "api.Item.require"


def test_reversed_iterator_keeps_later_mutations_visible(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    _, report = analyze("""
class Safe:
    def require(self): pass
class Risky:
    def require(self): raise Missing()
@router.get('/')
def endpoint():
    items = [Safe()]
    iterator = reversed(items)
    items.clear()
    items.append(Risky())
    for item in iterator: item.require()
""")
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"]


def test_custom_reverse_protocol_remains_explicitly_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    _, report = analyze("""
class Items:
    def __reversed__(self): raise Missing()
@router.get('/')
def endpoint(): return list(reversed(Items()))
""")
    [operation] = report["operations"]
    assert not operation["complete"]
    assert any(
        "unresolved reversed iteration protocol" in d["message"]
        for d in operation["diagnostics"]
    )


def test_iterable_filters_and_results_keep_independent_traces(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def values(item: int) -> list[int]:
    if item < 0: raise IterationFailure()
    return [item]
def allowed(item: int) -> bool:
    if item < 0: raise FilterFailure()
    return True
@router.get('/')
def endpoint(item_ids: list[int]):
    return [require_item(value) for item in item_ids for value in values(item) if allowed(value)]
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"] * 3
    assert {tuple(d["trace"]) for d in operation["diagnostics"]} == {
        ("api.endpoint", "api.values"),
        ("api.endpoint", "api.allowed"),
        ("api.endpoint", "api.require_item"),
    }


def test_async_eager_comprehension_follows_awaited_and_iterated_helpers(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
async def values() -> AsyncIterator[int]:
    yield 1
    raise IterationFailure()
async def allowed(item: int) -> bool:
    if item < 0: raise FilterFailure()
    return True
async def required(item: int) -> int:
    if item < 0: raise Missing()
    return item
@router.get('/')
async def endpoint():
    return [await required(item) async for item in values() if await allowed(item)]
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"] * 3
    assert {d["trace"][-1] for d in operation["diagnostics"]} == {
        "api.values",
        "api.allowed",
        "api.required",
    }


def test_surrounding_handler_catches_comprehension_errors(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(item_ids: list[int]):
    try:
        return [require_item(item) for item in item_ids]
    except Missing:
        return []
""")
    (operation,) = report["operations"]
    assert status == 0 and operation["complete"]
    assert operation["diagnostics"] == []


def test_comprehension_targets_do_not_leak_into_outer_environment(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(item_ids: list[int]):
    result = [item for require_item in item_ids for item in item_ids]
    return require_item(0)
""")
    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert status == 1 and operation["complete"]
    assert diagnostic["code"] == "CAN002"
    assert diagnostic["trace"] == ["api.endpoint", "api.require_item"]


def test_comprehension_targets_shadow_global_callable_names(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(callbacks: list, item_ids: list[int]):
    return [require_item(item) for require_item in callbacks for item in item_ids]
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}


def test_unconsumed_generator_expression_does_not_execute_body_or_filters(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def allowed(item: int) -> bool:
    raise FilterFailure()
@router.get('/')
def endpoint(item_ids: list[int]):
    pending = (require_item(item) for item in item_ids if allowed(item))
    return 1
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}
    assert any("deferred" in d["message"] for d in operation["diagnostics"])


def test_generator_creation_still_evaluates_outermost_iterable_expression(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def values() -> list[int]:
    raise IterationFailure()
@router.get('/')
def endpoint():
    pending = (require_item(item) for item in values())
    return 1
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN002", "CAN003"}
    (gap,) = [d for d in operation["diagnostics"] if d["code"] == "CAN002"]
    assert gap["trace"] == ["api.endpoint", "api.values"]


def test_explicit_iteration_follows_generator_expression_body(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(item_ids: list[int]):
    for item in (require_item(value) for value in item_ids):
        pass
    return 1
""")
    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert status == 1 and operation["complete"]
    assert diagnostic["code"] == "CAN002"
    assert diagnostic["trace"] == ["api.endpoint", "api.require_item"]


def test_custom_iteration_protocol_remains_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Custom:
    def __iter__(self):
        raise Missing()
        yield 1
@router.get('/')
def endpoint(source: Custom):
    return [item for item in source]
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}


def test_unawaited_coroutine_result_remains_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
async def required(item: int) -> int:
    raise Missing()
@router.get('/')
def endpoint(item_ids: list[int]):
    return [required(item) for item in item_ids]
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}


def test_later_target_is_lexically_local_in_earlier_filter(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(callbacks: list, item_ids: list[int]):
    return [item for item in item_ids if require_item(item) for require_item in callbacks]
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}


def test_first_iterable_uses_outer_binding_before_target_shadowing(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def values() -> list[int]:
    raise Missing()
@router.get('/')
def endpoint():
    return [values for values in values()]
""")
    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert status == 1 and operation["complete"]
    assert diagnostic["code"] == "CAN002"
    assert diagnostic["trace"] == ["api.endpoint", "api.values"]


def test_nested_eager_result_comprehension_follows_inner_body(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(groups: list[list[int]]):
    return [[require_item(item) for item in group] for group in groups]
""")
    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert status == 1 and operation["complete"]
    assert diagnostic["code"] == "CAN002"
    assert diagnostic["trace"] == ["api.endpoint", "api.require_item"]


def test_unknown_async_iteration_stays_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
async def endpoint(source: AsyncIterator[int]):
    return [item async for item in source]
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}


def test_named_expression_analyzes_the_assigned_expression(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(item_ids: list[int]):
    return [(seen := require_item(item)) for item in item_ids]
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN002"}


def test_unconsumed_async_generator_expression_keeps_awaits_deferred(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
async def required(item: int) -> int:
    raise Missing()
@router.get('/')
async def endpoint(item_ids: list[int]):
    pending = (await required(item) for item in item_ids)
    return 1
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}


def test_dict_comprehension_follows_both_key_and_value_expressions(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def key(item: int) -> int:
    if item < 0: raise Missing()
    return item
def value(item: int) -> int:
    if item < 0: raise FilterFailure()
    return item
@router.get('/')
def endpoint(item_ids: list[int]):
    return {key(item): value(item) for item in item_ids}
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002", "CAN002"]
    assert {d["trace"][-1] for d in operation["diagnostics"]} == {
        "api.key",
        "api.value",
    }


def test_destructured_comprehension_targets_are_local(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
@router.get('/')
def endpoint(pairs: list[tuple[int, int]]):
    return [require_item(item) for item, other in pairs]
""")
    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert status == 1 and operation["complete"]
    assert diagnostic["code"] == "CAN002"
    assert diagnostic["trace"] == ["api.endpoint", "api.require_item"]


def test_mutating_comprehension_targets_remain_incomplete(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Box:
    value: int
@router.get('/')
def endpoint(box: Box, item_ids: list[int]):
    return [1 for box.value in item_ids]
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN003"}
    assert any("target" in d["message"] for d in operation["diagnostics"])


def test_typed_element_method_dispatch_finds_the_application_error(
    analyze: Callable[..., tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Item:
    def validate(self) -> int:
        raise Missing()
@router.get('/')
def endpoint(items: list[Item]):
    return [item.validate() for item in items]
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN002"}
