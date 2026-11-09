# File uploads and downloads

Declare file operations on the same Canon `APIRouter` as other HTTP operations.
Their OpenAPI contracts drive the generated Python and TypeScript SDKs, including
canonical problem errors. This guide describes the unreleased source support.

## Install

```console
uv add "fastapi-canon[uploads]"
uv add --dev "fastapi-canon[codegen]"
```

The `uploads` extra installs `python-multipart`, which FastAPI needs to parse
uploaded files and form fields. Upload declarations remain ordinary HTTP routes;
there is no separate router or integration runtime. JSON-only applications do
not need this extra. Raw binary bodies and downloads do not need a multipart parser.

## Developer-facing API

Use `UploadFile` for one file and `list[UploadFile]` for multiple files under the
same multipart field name. FastAPI infers file inputs from these types:

```python
from typing import Annotated

from fastapi import Form, UploadFile
from fastapi_canon import APIRouter

router = APIRouter(namespace="files")


@router.post("/projects/{project_id}/files")
async def upload(
    project_id: str,
    files: list[UploadFile],
    title: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
) -> list[str]:
    return [file.filename or "unnamed" for file in files]
```

The generated operation is `client.files.upload(...)`. The example above returns
filenames; the [runnable application](../../examples/uploads/app.py) stores file
contents through an application-owned service resolved with `Inject[FileStore]`.
It also provides single-file upload and download operations.

Use `Annotated[UploadFile, File(alias="document", description="Report file")]`
when a file needs a wire alias or documentation, importing `File` from `fastapi`.
`file: UploadFile | None = None` declares an optional file. Separate named
`UploadFile` parameters declare separate file parts. Scalar form fields use
`Annotated[T, Form()]`; arrays of scalars become repeated fields.

`UploadFile` exposes the filename, declared content type and a spooled file with
async `read`, `seek` and `close` methods. Read in chunks when processing large
files. Consume or copy request-owned files before returning from the handler;
pass independently owned data or storage identifiers to background work.

Missing required parts and invalid form values use Canon's validation problems.
Declare application failures with `raises=[error]`, as for other HTTP routes.
Storage, authorization, content inspection and upload limits belong to the
application. A declared MIME type does not validate the file's contents.

## Generated clients

Python uses the SDK's `Upload(filename, content, content_type=...)` value:

```python
from api_client import ApiClient, Upload

async with ApiClient("http://localhost:8000") as client:
    with open("report.pdf", "rb") as report:
        uploaded = await client.files.upload_many(
            project_id="demo",
            files=[Upload("report.pdf", report, "application/pdf")],
            title="Quarterly report",
        )
    content = await client.files.download(project_id="demo", file_id=uploaded[0].id)
```

This excerpt runs inside an async function with the runnable application's
generated SDK. `content` is `bytes`. An `Upload` accepts bytes or a borrowed binary
handle; keep handles open until the await finishes and close them afterwards.
The SDK does not close borrowed handles after success, errors or cancellation.

TypeScript accepts native `File` or `Blob` values:

```typescript
import { createClient } from "./generated";

const client = createClient({ baseUrl: "http://localhost:8000" });
try {
  const uploaded = await client.files.uploadMany({
    projectId: "demo",
    files: [new File(["Notes"], "notes.txt", { type: "text/plain" })],
    title: "Attachments",
  });
  const content: Blob = await client.files.download({
    projectId: "demo", fileId: uploaded[0]!.id,
  });
  console.log(content.size);
} finally {
  await client.close();
}
```

`File` carries its filename and MIME type. A plain `Blob` uses the platform's
default filename. The HTTP library/fetch constructs the multipart boundary;
callers do not set `Content-Type` manually. Ordinary request options, timeouts,
cancellation, typed problems and TypeScript `.safe()` apply to file operations.

## Binary responses and raw uploads

Declare downloads with `response=CanonResponse.binary("application/pdf")` and
return a matching FastAPI `Response`, `FileResponse` or `StreamingResponse`.
`CanonResponse` describes the HTTP contract; the returned response supplies the
body and headers. See the [binary response example](../composition.md#binary-and-pdf-streams).
The runnable application uses `application/octet-stream` for its general download
operation and returns an encoded `Content-Disposition` filename.

Generated downloads return Python `bytes` or TypeScript `Blob`, including empty
files. Downloads are buffered in the SDK even when the server streams them.
Raw uploads use `Annotated[bytes, Body(media_type="application/octet-stream")]`
with `Body` imported from `fastapi`; the generated argument accepts Python bytes
or a TypeScript Blob. The current SDK API exposes neither download streaming nor
upload progress or resumable transfers.

## Run the example

From the repository root after [development setup](../contributing.md):

```console
uv run --frozen uvicorn examples.uploads.app:app --reload
```

Upload one file, then several files under the same field name:

```console
curl --form "file=@report.pdf" --form "title=Report" http://localhost:8000/projects/demo/file
curl --form "files=@report.pdf" --form "files=@notes.txt" --form "title=Attachments" http://localhost:8000/projects/demo/files
```

Use an ID from the response at
`GET /projects/demo/files/{file_id}/content`. The demo stores files in memory and
loses them when the process restarts; it buffers uploads to keep the example small.

Generate and check both SDKs using the supplied configuration:

```console
uv run --frozen fastapi-canon generate --config examples/uploads/fastapi-canon.toml
uv run --frozen fastapi-canon generate --config examples/uploads/fastapi-canon.toml --check
```

Contracts and clients are written under the root's ignored `dist/uploads/`.

## Supported multipart contracts

The common generator supports flat named file and scalar parts, including arrays
of either. Repeated parts preserve order. Optional parts may be omitted;
`None`/`null` omits an optional part. Empty arrays send no parts, so a required
file list may be rejected by the server. Empty files remain actual file parts.

Nested object/array parts, JSON metadata parts, custom per-part headers and
non-default encodings fail generation with a source-located diagnostic. Model
complex metadata as a separate JSON operation or flat form fields for this API.
The [generator guide](../unified-canon-concept.md#multipart-uploads-and-binary-transfers)
records the precise contract restrictions. Review each diagnostic before excluding
an operation; supported file operations participate in the ordinary generated SDK.
