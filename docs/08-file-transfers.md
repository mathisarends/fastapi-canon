# 8. File transfers

Upload files, stream raw bodies and serve downloads on the same `APIRouter`.
Install `fastapi-canon[uploads]` for multipart forms.

## Uploads

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
) -> list[str]:
    return [file.filename or "unnamed" for file in files]
```

This is plain FastAPI; Canon adds problem responses for invalid forms and the
OpenAPI contract for your HTTP client generator. Read large files in chunks and
finish with them before the handler returns.

## Limits

```python
@router.post("/reports", max_body_size=10_000_000, form_limits={"max_files": 5}, http_statuses=[413])
```

Oversized bodies return a 413 problem, too many files a 400 problem.

## Streaming uploads

Consume a raw body as it arrives:

```python
from collections.abc import AsyncIterator

from fastapi_canon import BinaryBody


@router.put("/files")
async def upload_raw(
    chunks: Annotated[AsyncIterator[bytes], BinaryBody("application/octet-stream")],
) -> int:
    size = 0
    async for chunk in chunks:
        size += len(chunk)
    return size
```

## Downloads

Declare the media type with `CanonResponse` and return any FastAPI response:

```python
from fastapi.responses import FileResponse
from fastapi_canon import CanonResponse


@router.get(
    "/documents/{document_id}",
    raises=[document_missing],
    response=CanonResponse.binary("application/pdf", headers=["Content-Disposition"]),
)
async def document(document_id: UUID) -> FileResponse:
    return FileResponse(storage.path(document_id), media_type="application/pdf")
```

Check access before returning: once a download has started, an error can only
cut it short. More response contracts are in the
[router reference](20-routers.md#success-response-contracts).

Runnable: [`examples/uploads`](../examples/uploads/app.py)
(`uv run --frozen uvicorn examples.uploads.app:app --reload`).

---

Previous: [7. Error checking](07-error-checking.md) · [Learning path](README.md) · Next: [9. Database](09-database.md)
