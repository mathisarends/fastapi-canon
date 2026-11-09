# File uploads and downloads

Declare file operations on the same Canon `APIRouter` as other HTTP operations.
Their OpenAPI contracts are inputs for your chosen HTTP client generator, including
canonical problem schemas. This guide covers Canon 0.17.0 and later.

## Install

```console
uv add "fastapi-canon[uploads]"
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

The example exports its file/form contract in OpenAPI and returns
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

## HTTP clients

Use your OpenAPI generator's multipart and binary support. Canon does not generate
HTTP clients or impose a client-side upload representation. Configure Orval or
another tool against OpenAPI and include its generate/check commands in the
[workflow](../codegen.md).

## Binary responses and raw uploads

Declare downloads with `response=CanonResponse.binary("application/pdf")` and
return a matching FastAPI `Response`, `FileResponse` or `StreamingResponse`.
`CanonResponse` describes the HTTP contract; the returned response supplies the
body and headers. See the [binary response example](../composition.md#binary-and-pdf-streams).
The runnable application uses `application/octet-stream` for its general download
operation and returns an encoded `Content-Disposition` filename.

Raw uploads use `Annotated[bytes, Body(media_type="application/octet-stream")]`
with `Body` imported from FastAPI. Buffering, upload progress and cancellation
on the client depend on the chosen HTTP tooling.

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

Export the contract for your HTTP generator:

```console
uv run fastapi-canon contracts export --app examples.uploads.app:app --output-dir dist/uploads/contracts --kind openapi
```

Server file/form declarations remain ordinary FastAPI operations. Client-side
multipart limitations are determined by the external HTTP generator.
