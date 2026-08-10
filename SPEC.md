# FastAPI Canon – Repository Specification

## Purpose

This repository captures reusable FastAPI engineering conventions extracted from real projects.

When pointed at one or more existing projects, inspect their implementation and update this repository with patterns that are:

- reusable across projects
- intentional and architecturally relevant
- worth preserving as a convention or reference

Do not document project-specific business logic.

## Output structure

### `canon.md`

The concise normative reference.

Add rules that should generally be followed in new FastAPI projects.

Write prescriptively and compactly:

```md
## Dependency Injection

Use constructor injection for application services.

FastAPI `Depends` must not leak beyond the presentation layer.

Prefer rules over explanations.

Target: something an agent can read quickly before implementing a project.

guides/architecture.md

Architecture and code-organization patterns.

Examples:

package / feature structure
layer boundaries
application/domain/infrastructure/presentation separation
dependency direction
service and repository responsibilities

Include rationale only where the rule is not obvious.

guides/database.md

Persistence conventions.

Examples:

SQLAlchemy patterns
sessions / transactions
repository implementations
migrations
model boundaries
database-specific dependency injection
guides/tooling.md

Development and infrastructure conventions.

Examples:

pyproject.toml
dependency management
Ruff / mypy / pytest
environment configuration
Docker
development commands
CI conventions
examples/snippets/

Small isolated examples of individual patterns.

Create a directory only when executable/reference code communicates the pattern better than Markdown.

Examples:

snippets/
├── dependency-injection/
├── error-handling/
└── database-session/

Keep snippets minimal. Do not reproduce entire source files unnecessarily.

examples/reference-service/

A small coherent FastAPI service demonstrating how the canon fits together.

It should contain representative examples of the major conventions, but no unnecessary business functionality.

Prefer evolving one reference service over creating multiple sample applications.

Writing rules
Be concise.
Do not turn the repository into a FastAPI tutorial.
Do not explain basic Python or FastAPI concepts.
Prefer concrete rules and code over prose.
Avoid duplicating the same guidance across files.
canon.md is authoritative; guides provide depth.
Examples demonstrate rules; they do not define new rules implicitly.
Preserve useful existing content unless the inspected projects provide a clearly better pattern.
Extraction behavior

When inspecting a project:

Identify noteworthy architectural and implementation patterns.
Compare them with the existing canon.
Ignore incidental or project-specific choices.
Add genuinely reusable conventions that are missing.
Improve existing guidance when the project demonstrates a better implementation.
Add or update example code where useful.

Do not blindly copy the source project's architecture.

The goal is to distill the best reusable pattern, not archive the project.

Scope

Focus primarily on:

FastAPI API design
application architecture
dependency injection
persistence
configuration
validation and serialization
error handling
testing
observability
developer tooling

Patterns outside this scope should only be added when they materially affect how a FastAPI service should be structured.
```
