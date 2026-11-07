from typing import Any

from fastapi_canon.codegen.ir import GenerationError, Parameter


def binary_schema(schema: dict[str, Any]) -> bool:
    return (
        schema.get("type") == "string"
        and "contentEncoding" not in schema
        and (
            schema.get("format") == "binary"
            or schema.get("contentMediaType") == "application/octet-stream"
        )
    )


def read_http_body(
    document: dict[str, Any], body: dict[str, Any]
) -> tuple[list[Parameter], str]:
    from fastapi_canon.integrations.sse.codegen.openapi import resolve_ref

    media = body.get("content", {})
    if len(media) != 1:
        raise _unsupported("request bodies require exactly one supported media type")
    media_type, content = next(iter(media.items()))
    schema = content.get("schema", {})
    required = body.get("required", False)
    if media_type == "application/json":
        return [Parameter("body", "body", schema, required, "body")], media_type
    schema = resolve_ref(document, schema)
    if media_type == "application/octet-stream" and binary_schema(schema):
        return [
            Parameter(
                "body",
                "body",
                {"type": "string", "x-canon-binary": True},
                required,
                "body",
            )
        ], media_type
    if media_type != "multipart/form-data":
        raise _unsupported("unsupported request body media type or binary schema")
    properties = schema.get("properties", {})
    if (
        schema.get("type") != "object"
        or not properties
        or set(schema)
        & {
            "allOf",
            "oneOf",
            "anyOf",
            "patternProperties",
            "if",
            "then",
            "else",
            "dependentRequired",
            "dependentSchemas",
            "not",
        }
        or schema.get("additionalProperties", False) is not False
        or set(schema.get("required", [])) - properties.keys()
        or (not required and schema.get("required"))
    ):
        raise _unsupported(
            "multipart requires a flat object with named, unconditional parts"
        )
    encodings = content.get("encoding", {})
    if encodings.keys() - properties.keys():
        raise _unsupported("multipart encoding names an unknown part")
    parameters = []
    for name, raw in properties.items():
        part = resolve_ref(document, raw)
        # Null has no portable multipart representation: optional nulls are omitted.
        branches = part.get("anyOf") or part.get("oneOf") or []
        non_null = [branch for branch in branches if branch.get("type") != "null"]
        if len(non_null) == 1:
            part = resolve_ref(document, non_null[0])
        item = (
            resolve_ref(document, part.get("items", {}))
            if part.get("type") == "array"
            else part
        )
        file = binary_schema(item)
        if set(part) & {"allOf", "oneOf", "anyOf", "prefixItems"} or set(item) & {
            "allOf",
            "oneOf",
            "anyOf",
        }:
            raise _unsupported(f"unsupported multipart composition for {name!r}")
        if not file and (
            not isinstance(item.get("type"), str)
            or item.get("type") not in {"string", "integer", "number", "boolean"}
            or item.get("format") == "binary"
            or set(item)
            & {"allOf", "oneOf", "anyOf", "contentEncoding", "contentMediaType"}
        ):
            raise _unsupported(
                f"unsupported multipart part {name!r}; use scalar or file parts"
            )
        encoding = encodings.get(name, {})
        allowed_type = "application/octet-stream" if file else "text/plain"
        if (
            set(encoding) - {"contentType", "style", "explode", "allowReserved"}
            or encoding.get("contentType", allowed_type) != allowed_type
            or encoding.get("style", "form") != "form"
            or encoding.get("explode", True) is not True
            or encoding.get("allowReserved", False)
        ):
            raise _unsupported(f"unsupported multipart encoding for {name!r}")
        parameters.append(
            Parameter(
                name,
                name,
                part,
                name in schema.get("required", []),
                "file" if file else "form",
            )
        )
    return parameters, media_type


def _unsupported(message: str) -> GenerationError:
    return GenerationError(message, code="canon.codegen.E002")
