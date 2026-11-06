# DX-030: Reduce the root facade to application-building names

- Status: Open
- Priority: P2
- Kind: Feature (breaking public API)
- Baseline: `df05e84` (2026-11-06)
- Depends on: [DX-023](DX-023-public-export-organization.md)
- Earlier review: S1

## Problem and reproduction

Current workflow inspection, not a bug:

```console
uv run --frozen python -c "import fastapi_canon; print(len(fastapi_canon.__all__))"
```

The root exports 43 names; 19 are never imported by documented consumers
(see the [DX-023 inventory](DX-023-public-export-organization.md#inventory)).
It mixes application building blocks with diagnostics result types, a
standard-library re-export (`HTTPMethod`), a second router class (`CanonRouter`),
type aliases and optional database integration names, and exposes `RpcChannel`
under two paths.

## Scope

Apply the [root decision](DX-023-public-export-organization.md#root-fastapi_canon):
keep `APIRouter`, `Feature`, `Composition`, `RouterPolicy`, `Inject`,
`ServiceScope`, `Scoped`, `Error`, `ErrorOptions`, `ExceptionHandlerSpec`,
`CanonResponse`, `ResponseHeaders`, `export_contracts` and `__version__`; remove
every other root export to its documented namespace or make it internal.

Entry points: [`fastapi_canon/__init__.py`](../fastapi_canon/__init__.py),
[`public_exports.py`](../fastapi_canon/public_exports.py),
[`exceptions.py`](../fastapi_canon/exceptions.py), `docs/canon.md`, README,
`docs/`, `examples/`, the bundled skill under `fastapi_canon/skills/` and
`scripts/smoke_installed.py`.

Out of scope: WebSocket, SSE and WebRTC facades (DX-031, DX-032).

## Acceptance criteria

- [ ] `fastapi_canon.__all__` contains exactly the kept names, and every removed
      name raises `AttributeError`/`ImportError` from the root. No aliases.
- [ ] Removed names import from their target: `fastapi_canon.diagnostics`,
      `.error`, `.exceptions`, `.openapi`, `.contracts`,
      `.integrations.database`, `.integrations.websockets`, or `http.HTTPMethod`.
- [ ] `FeatureConfigurationError` and `ResponseConfigurationError` are defined in
      `exceptions.py` (moved, not re-exported from `feature.py`/`response.py`), so
      `exceptions` keeps no dependency on composition or response modules.
- [ ] `CanonRouter`, `ProviderFactory`, `ProviderSource` and `RouterFactory` are no
      longer exported anywhere public; internal users import them by module path.
      Public signatures of `Feature`, `Composition` and `Composition.override()`
      spell out `Provider | Callable[[], Provider]` and `Callable[[], APIRouter]`
      instead of the aliases. No public signature references `CanonRouter`.
- [ ] The root no longer needs `public_exports.__getattr__`; remove the module if
      nothing else uses it. `import fastapi_canon` and root CLI help still load no
      optional runtime (database, websockets, codegen, telemetry, media engines).
- [ ] `docs/canon.md` lists the public submodules from DX-023 and states that
      other modules are internal. README, guides, examples, skill and smoke
      script use the new paths; `rg "from fastapi_canon import"` shows only kept names.
- [ ] `CHANGELOG.md` `### Breaking changes` contains the root old/new import table.
      Release impact: next minor (see DX-023 release note).

## Verification

- Focused: an import-surface unit test asserting the root `__all__` set and that
  removed names are absent; the existing lazy-import tests for core imports and CLI help.
- Rerun the DX-023 inventory and record that consumer imports use only kept names.
- Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
