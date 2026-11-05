"""Export the contracts of a composed application without starting its runtime."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI

from fastapi_canon.openapi import export_openapi

type ContractKind = Literal["openapi", "openrpc", "webrtc"]


@dataclass(frozen=True, slots=True)
class Contracts:
    """An application contract snapshot; absent or unselected transports are None."""

    openapi: dict[str, Any] | None = None
    openrpc: dict[str, Any] | None = None
    webrtc: dict[str, Any] | None = None

    @property
    def documents(self) -> dict[str, dict[str, Any]]:
        """Documents keyed by their stable export filenames without extensions."""
        return {
            kind: document
            for kind in ("openapi", "openrpc", "webrtc")
            if (document := getattr(self, kind)) is not None
        }


def export_contracts(
    app: FastAPI,
    *,
    kinds: Sequence[ContractKind] = ("openapi", "openrpc", "webrtc"),
    base_url: str = "{baseUrl}",
) -> Contracts:
    """Export registered HTTP/SSE, RPC and WebRTC declarations.

    This never enters the lifespan or resolves dependencies. The application
    must already be composed. Its factory and module imports still execute
    normally; keep runtime settings and resource acquisition out of those steps.
    ``base_url`` supplies the public RPC origin, independently of mounted paths.
    Its default is a client-supplied ``baseUrl`` template.
    """
    if not isinstance(app, FastAPI):
        raise TypeError("export_contracts requires a FastAPI application")
    selected = tuple(kinds)
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(kind not in {"openapi", "openrpc", "webrtc"} for kind in selected)
    ):
        raise ValueError(
            "kinds must select unique openapi, openrpc or webrtc contracts"
        )
    documents: dict[str, Any] = {}
    if "openapi" in selected:
        documents["openapi"] = export_openapi(app)
    if "openrpc" in selected:
        from fastapi_canon.integrations.websockets.schema.application import (
            export_rpc_contract,
        )

        documents["openrpc"] = export_rpc_contract(app, base_url=base_url)
    if "webrtc" in selected:
        from fastapi_canon.integrations.webrtc.contract import build_contract

        document = build_contract(app)
        if document["sessions"]:
            documents["webrtc"] = document
    return Contracts(**documents)
