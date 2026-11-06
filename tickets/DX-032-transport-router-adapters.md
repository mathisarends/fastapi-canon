# DX-032: Make SSE and WebRTC routers internal adapters of APIRouter

- Status: Open
- Priority: P2
- Kind: Feature (breaking public API)
- Baseline: `df05e84` (2026-11-06)
- Depends on: [DX-023](DX-023-public-export-organization.md)
- Earlier review: R1, S2

## Problem and reproduction

Current workflow inspection, not a bug. [Routers](../docs/routers.md) and
[canon.md](../docs/canon.md) present one `APIRouter` per feature with `sse()`,
`include_channel()` and `webrtc()`. The SSE and WebRTC facades still export
`StreamRouter`, `CanonStreamRouter`, `WebRtcRouter` and `CanonWebRtcRouter`:

- `examples/sse/agent_runs/__init__.py` declares its streams on `StreamRouter`.
- `examples/webrtc/remote_video/app.py` and `examples/webrtc/practice/app.py`
  declare sessions on `WebRtcRouter`.
- `APIRouter.webrtc()` constructs one `CanonWebRtcRouter` per declaration
  ([api_router.py](../fastapi_canon/api_router.py)); `APIRouter.sse()` does the same
  with `CanonStreamRouter`.

Two declaration paths exist for the same transport. One capability exists only on
the specialized router: `WebRtcRouter(max_sessions=n)` limits all sessions of
that router together, and `WebRtcRouter.active_sessions` reports their count.
`APIRouter.webrtc()` only accepts per-declaration `max_sessions`.

## Scope

1. Add `SessionCapacity` to the WebRTC facade and `capacity=` to
   `APIRouter.webrtc()`, before removing `WebRtcRouter`.
2. Remove the four router classes from their facades; they stay as internal
   adapters used by `APIRouter`.
3. Move WebRTC engine-authoring types to `fastapi_canon.integrations.webrtc.engines`
   as decided in [DX-023](DX-023-public-export-organization.md#sse-and-webrtc-facades).
4. Migrate examples, docs, smoke scripts and generated example clients.

Out of scope: WebRTC signaling protocol (`fastapi-webrtc.v1`), SSE terminal event
format, `CanonWebSocketRouter` (stays public for raw sockets and binary streams).

## Acceptance criteria

Shared capacity (first commit):

- [ ] `SessionCapacity(max_sessions=n)` rejects `n <= 0` like today's
      `max_sessions`. `@router.webrtc(..., capacity=capacity)` accepts one shared
      instance across declarations, routers and features.
- [ ] Admission takes a slot from the shared capacity and the declaration's own
      `max_sessions`; a full capacity refuses the session exactly as a full
      router does today (`ErrorCode.SESSION_LIMIT`, a `REJECTED` session event,
      no peer created). Slots are released on every cleanup path, including
      refusal, negotiation failure and shutdown.
- [ ] `capacity.active` reports the number of held slots and replaces
      `WebRtcRouter.active_sessions`.
- [ ] Integration test with real collaborators: two endpoints on different
      routers sharing `SessionCapacity(max_sessions=1)`; the second session is
      refused while the first is open and admitted after it closes, with finite deadlines.
- [ ] Docs state that one instance is shared by every application in the process
      that declares it (for example, several test apps importing one module).

Facade changes:

- [ ] `StreamRouter`, `CanonStreamRouter`, `WebRtcRouter` and `CanonWebRtcRouter`
      are absent from all public `__all__` lists. No aliases. Tests may import them
      by internal module path.
- [ ] Engine-authoring types are exported from `webrtc/engines/__init__.py` and no
      longer from the WebRTC facade. The package imports no engine adapter (aiortc)
      eagerly; core imports and CLI help still load no media engine.
- [ ] The WebRTC and SSE facades drop `__version__`.
- [ ] `examples/sse/agent_runs` uses `APIRouter.sse()`; its committed OpenAPI
      fixture and generated SDKs are regenerated through the generators. The
      examples switch to Canon terminal events (`canon.complete`/`canon.problem`);
      record this example contract change.
- [ ] `examples/webrtc/*`, `scripts/smoke_installed.py`, `docs/`, the bundled
      skill and `docs/canon.md` use `APIRouter.webrtc()`/`sse()`. Plain SSE
      without Canon terminal events points to FastAPI's native SSE support
      ([native transports](../docs/integrations/sse/native-transports.md)).
- [ ] `CHANGELOG.md` `### Breaking changes` includes the migration:
      `WebRtcRouter(...)` + `@rtc.session(path, ...)` becomes
      `@router.webrtc(path, ...)`; router-level `engine`, `ice_servers`,
      `on_event`, `allowed_origins` and `limits` move to each declaration;
      `WebRtcRouter(max_sessions=n)` with several sessions becomes a shared
      `capacity=SessionCapacity(max_sessions=n)`; `StreamRouter`/`CanonStreamRouter`
      `.stream()` becomes `APIRouter.sse()`; engine types import from `.engines`.

## Verification

- `uv run --frozen pytest tests/unit/integrations/webrtc tests/integration/integrations/webrtc tests/contract/integrations/webrtc tests/unit/integrations/sse`
- `uv run --frozen python scripts/sse/codegen.py`, then `uv run --frozen python scripts/check.py clients`
- `uv run --locked python scripts/check_webrtc_artifacts.py` and
  `uv run --locked python scripts/check_streams_artifacts.py`
- Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
