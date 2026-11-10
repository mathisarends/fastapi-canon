import warnings
from copy import deepcopy
from typing import Annotated, Literal

import pytest
from fastapi_canon.integrations.sse.codegen.diagnostics import (
    OptionalDiscriminatorWarning,
)
from fastapi_canon.integrations.sse.codegen.ir import Object, build_ir
from fastapi_canon.integrations.sse.codegen.openapi import ContractError
from pydantic import ConfigDict, Field, create_model
from tests.unit.integrations.sse.codegen.helpers import event_contract, event_document


def test_operation_selection_omits_unreachable_models_and_diagnostics():
    document = event_document(str)
    document["paths"]["/ignored"] = deepcopy(document["paths"]["/events"])
    ignored = document["paths"]["/ignored"]["get"]
    ignored["operationId"] = "ignored"
    content = ignored["responses"]["200"]["content"]["text/event-stream"]
    content["itemSchema"]["properties"]["data"]["contentSchema"] = {
        "$ref": "#/components/schemas/Ignored"
    }
    document.setdefault("components", {}).setdefault("schemas", {})["Ignored"] = {
        "type": "string",
        "format": "uri-reference",
    }
    diagnostics = []
    contract = build_ir(
        document, include_operations=("events",), diagnostics=diagnostics
    )
    assert [operation.operation_id for operation in contract.operations] == ["events"]
    assert not contract.declarations and not diagnostics


@pytest.mark.parametrize("selection", [("missing",), (), ("events", "events")])
def test_invalid_operation_selection_is_rejected(selection):
    with pytest.raises(ContractError, match="operation"):
        build_ir(event_document(str), include_operations=selection)


@pytest.mark.parametrize("required_defaults", [False, True])
def test_default_discriminator_warns_only_when_optional_in_output(required_defaults):
    config = ConfigDict(
        json_schema_mode_override="serialization",
        json_schema_serialization_defaults_required=required_defaults,
    )
    first = create_model("First", __config__=config, kind=(Literal["first"], "first"))
    last = create_model("Last", kind=(Literal["last"], ...))
    event = Annotated[first | last, Field(discriminator="kind")]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        contract = event_contract(event)
    assert len(caught) == (0 if required_defaults else 1)
    if caught:
        assert caught[0].category is OptionalDiscriminatorWarning
        message = str(caught[0].message)
        for part in ("events", "GET /events", "First", "kind", "remains optional"):
            assert part in message
    model = next(item.type for item in contract.declarations if item.name == "First")
    assert isinstance(model, Object)
    assert model.fields[0].required is required_defaults


def test_nested_and_recursive_event_unions_warn_once_per_model():
    first = create_model("First", kind=(Literal["first"], "first"))
    last = create_model("Last", kind=(Literal["last"], ...))
    event = Annotated[first | last, Field(discriminator="kind")]
    envelope = create_model("Envelope", values=(list[event], ...))
    document = event_document(envelope)
    model = document["components"]["schemas"]["Envelope"]
    model["properties"]["child"] = {"$ref": "#/components/schemas/Envelope"}
    with pytest.warns(OptionalDiscriminatorWarning, match="First.*kind") as caught:
        build_ir(document)
    assert len(caught) == 1


def test_optional_request_discriminator_does_not_trigger_event_warning():
    first = create_model("First", kind=(Literal["first"], "first"))
    last = create_model("Last", kind=(Literal["last"], ...))
    request = Annotated[first | last, Field(discriminator="kind")]
    document = event_document(request)
    operation = document["paths"]["/events"].pop("get")
    document["paths"]["/events"]["post"] = operation
    media = operation["responses"]["200"]["content"]["text/event-stream"]
    data = media["itemSchema"]["properties"]["data"]
    operation["requestBody"] = {
        "content": {"application/json": {"schema": data["contentSchema"]}}
    }
    data["contentSchema"] = {"type": "string"}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_ir(document)
    assert not caught
