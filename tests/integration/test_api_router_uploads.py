import asyncio

from httpx import ASGITransport, AsyncClient

from examples.uploads.app import create_app


async def test_single_and_repeated_uploads_preserve_fields_and_download_bytes() -> None:
    app = create_app()
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        single = await client.post(
            "/projects/demo/file",
            files={"file": ('résumé".bin', b"\x00\xffCanon", "application/pdf")},
            data={"title": "Report", "description": "First upload"},
        )
        assert single.status_code == 200, single.text
        file = single.json()
        assert file == {
            "id": file["id"],
            "filename": "résumé%22.bin",
            "content_type": "application/pdf",
            "title": "Report",
            "description": "First upload",
            "size": 7,
        }
        downloaded = await client.get(f"/projects/demo/files/{file['id']}/content")
        assert downloaded.status_code == 200
        assert downloaded.content == b"\x00\xffCanon"
        assert downloaded.headers["content-type"] == "application/octet-stream"
        assert downloaded.headers["content-disposition"] == (
            "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9%2522.bin"
        )
        multiple = await client.post(
            "/projects/demo/files",
            files=[
                ("files", ("first.txt", b"first", "text/plain")),
                ("files", ("empty.bin", b"", "application/octet-stream")),
            ],
            data={"title": "Attachments"},
        )
        assert multiple.status_code == 200, multiple.text
        first, empty = multiple.json()
        assert [first["filename"], empty["filename"]] == ["first.txt", "empty.bin"]
        assert first["size"] == 5 and empty["size"] == 0
        assert first["title"] == empty["title"] == "Attachments"
        assert first["description"] is None and empty["description"] is None
        empty_download = await client.get(f"/projects/demo/files/{empty['id']}/content")
        assert empty_download.status_code == 200
        assert empty_download.content == b""
        other_project = await client.get(f"/projects/other/files/{file['id']}/content")
        assert other_project.status_code == 404
        assert other_project.headers["content-type"] == "application/problem+json"
        assert other_project.json()["code"] == "file_missing"


async def test_missing_upload_fields_use_canonical_validation_problems() -> None:
    app = create_app()
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/projects/demo/files", files={"files": ("report.txt", b"report")}
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        problem = response.json()
        assert problem["status"] == 422
        assert problem["code"] == "request_validation_error"
        assert problem["errors"] == [
            {"code": "missing", "detail": "Field required", "pointer": "#/title"}
        ]
