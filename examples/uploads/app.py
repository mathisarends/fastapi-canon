from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from dishka import Provider, Scope, provide
from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from fastapi_canon import APIRouter, CanonResponse, Composition, Error, Feature, Inject

__all__ = ["app", "create_app"]


class FileMissing(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    id: UUID
    filename: str
    content_type: str
    title: str
    description: str | None
    content: bytes


class FileStore:
    """Application-owned, in-memory storage for this example."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, UUID], StoredFile] = {}

    def put(self, project_id: str, file: StoredFile) -> None:
        self.files[project_id, file.id] = file

    def get(self, project_id: str, file_id: UUID) -> StoredFile:
        try:
            return self.files[project_id, file_id]
        except KeyError:
            raise FileMissing from None


class UploadProvider(Provider):
    store = provide(FileStore, scope=Scope.APP)


class FileInfo(BaseModel):
    id: UUID
    filename: str
    content_type: str
    title: str
    description: str | None
    size: int


def create_app() -> FastAPI:
    router = APIRouter(namespace="files")
    missing = Error(
        FileMissing, status=404, code="file_missing", title="File not found"
    )

    @router.post("/projects/{project_id}/file")
    async def upload(
        project_id: str,
        file: UploadFile,
        title: Annotated[str, Form()],
        store: Inject[FileStore],
        description: Annotated[str | None, Form()] = None,
    ) -> FileInfo:
        return await _save_file(store, project_id, file, title, description)

    @router.post("/projects/{project_id}/files")
    async def upload_many(
        project_id: str,
        files: list[UploadFile],
        title: Annotated[str, Form()],
        store: Inject[FileStore],
        description: Annotated[str | None, Form()] = None,
    ) -> list[FileInfo]:
        return [
            await _save_file(store, project_id, file, title, description)
            for file in files
        ]

    @router.get(
        "/projects/{project_id}/files/{file_id}/content",
        response=CanonResponse.binary(
            "application/octet-stream", headers=["Content-Disposition"]
        ),
        raises=[missing],
    )
    async def download(
        project_id: str, file_id: UUID, store: Inject[FileStore]
    ) -> Response:
        file = store.get(project_id, file_id)
        filename = quote(file.filename, safe="")
        return Response(
            file.content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )

    return Composition(
        features=(Feature(name="files", routers=[router], providers=[UploadProvider]),)
    ).build(FastAPI(title="Canon file uploads"))


async def _save_file(
    store: FileStore,
    project_id: str,
    upload: UploadFile,
    title: str,
    description: str | None,
) -> FileInfo:
    file = StoredFile(
        id=uuid4(),
        filename=upload.filename or "upload",
        content_type=upload.content_type or "application/octet-stream",
        title=title,
        description=description,
        content=await upload.read(),
    )
    store.put(project_id, file)
    return FileInfo(
        id=file.id,
        filename=file.filename,
        content_type=file.content_type,
        title=file.title,
        description=file.description,
        size=len(file.content),
    )


app = create_app()
