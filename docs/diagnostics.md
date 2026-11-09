# Explain diagnostic codes

`fastapi-canon explain CODE` gives the meaning, a minimal example, a repair and
an authoritative guide for a built-in code. This page describes the prepared
source interface; see [Unreleased changes](../CHANGELOG.md#unreleased) and the
[publication record](releases.md#current-versions).

```console
fastapi-canon explain CAN001
fastapi-canon explain CAN003
fastapi-canon explain canon.routing.E002
fastapi-canon explain canon.routing.E002 --format json
```

Copy the code exactly as printed: names are case-sensitive. Lookup is offline
and never imports an application, starts its lifespan, runs a checker or loads
an optional generator/media/MCP/telemetry runtime. It explains the code's scope;
it does not establish that a particular application's finding is repaired.

Known codes exit 0. Unknown codes exit 2 with a hint to the command help.
Application-defined codes such as `projects.W001` use their own `CheckIssue.hint`
and application documentation, rather than a built-in catalogue entry.

## Code families and scope

| Family | Meaning and authoritative guide |
| --- | --- |
| `CAN001` | A known Error mapping is missing from an operation's `raises`; see [source checking](error-checking.md). |
| `CAN002` | A supported explicit application exception lacks a known mapping; see [source checking](error-checking.md). |
| `CAN003` | Source analysis is incomplete; see [analysis boundaries](error-checking.md#analysis-boundaries). |
| `canon.*` composition codes | Configuration, routing, provider-root and contract checks; see [findings and coverage](system-checks.md#findings-and-coverage). |

The catalogue also links specific [RPC roots](system-checks.md#rpc-method-dependency-preflight),
[HTTP/SSE roots](system-checks.md#http-and-sse-endpoint-dependency-preflight),
[WebRTC factories](system-checks.md#webrtc-factory-dependency-preflight) and
[feature checks](system-checks.md#add-a-feature-check) when those scopes matter.
Existing emitted code names remain unchanged. Explanations are static CLI data;
source analysis stays independent of composition and framework runtimes.

## JSON explanation contract

`--format json` writes one JSON object on stdout. A successful lookup has:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`. |
| `code` | The exact built-in code requested. |
| `meaning` | The diagnostic's purpose and scope. |
| `example` | A minimal code/configuration example or concrete failing scenario. |
| `repair` | A corrective action and any relevant coverage limit. |
| `guide` | A direct URL to the authoritative repository guide. |

An unknown lookup keeps `schema_version` and `code`, and supplies an `error`
string instead of the explanation fields. The process still exits 2. Invalid
arguments use the normal command-line usage error. Text is the default;
`fastapi-canon explain --help` lists syntax, examples and exit policy.
