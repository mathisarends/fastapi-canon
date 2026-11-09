import asyncio
import copy
import importlib
import json
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Body, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from fastapi_canon import (
    APIRouter,
    CanonResponse,
    Composition,
    Error,
    Feature,
    export_contracts,
)
from tests.unit.integrations.webrtc.server import serve

ROOT = Path(__file__).resolve().parents[3]
PAYLOAD = b"\x00\xff\x80Canon\r\n"


class UploadRejected(Exception):
    pass


class Blob(BaseModel):
    name: str


@pytest.fixture(scope="module")
def file_app() -> FastAPI:
    router = APIRouter(namespace="files")
    rejected = Error(
        UploadRejected, status=413, code="upload_rejected", title="Upload rejected"
    )

    @router.post("/upload", raises=[rejected])
    async def upload(
        document: UploadFile,
        attachments: list[UploadFile],
        title: Annotated[str, Form()],
        tags: Annotated[list[str], Form()],
        enabled: Annotated[bool, Form()],
        count: Annotated[int, Form()],
        note: Annotated[str | None, Form()] = None,
        thumbnail: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, Any]:
        if title == "reject":
            raise UploadRejected
        return {
            "files": [
                {
                    "name": item.filename,
                    "type": item.content_type,
                    "hex": (await item.read()).hex(),
                }
                for item in [document, *attachments]
            ],
            "title": title,
            "tags": tags,
            "enabled": enabled,
            "count": count,
            "note": note,
            "thumbnail": thumbnail.filename if thumbnail else None,
        }

    @router.post(
        "/raw",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    async def raw(
        body: Annotated[bytes, Body(media_type="application/octet-stream")] = b"",
    ) -> Response:
        return Response(body, media_type="application/octet-stream")

    @router.get(
        "/download",
        response=CanonResponse.binary("application/pdf"),
    )
    async def download() -> Response:
        return Response(PAYLOAD, media_type="application/pdf")

    @router.get(
        "/slow",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    async def slow() -> Response:
        async def chunks() -> AsyncIterator[bytes]:
            yield PAYLOAD
            await asyncio.sleep(2)
            yield PAYLOAD

        return StreamingResponse(chunks(), media_type="application/octet-stream")

    @router.get(
        "/mixed",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            },
            201: {"model": Blob},
            204: {"description": "No file"},
        },
    )
    async def mixed(mode: str = "binary") -> Response:
        if mode == "json":
            return JSONResponse({"name": "report.pdf"}, status_code=201)
        if mode == "empty":
            return Response(status_code=204)
        return Response(PAYLOAD, media_type="application/octet-stream")

    @router.post("/optional")
    async def optional(
        thumbnail: Annotated[UploadFile | None, File()] = None,
        note: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        return {"file": thumbnail.filename if thumbnail else None, "note": note}

    @router.post("/metadata")
    async def metadata(body: Blob) -> Blob:
        return body

    return Composition(features=[Feature(name="files", routers=[router])]).build(
        FastAPI()
    )


@pytest.fixture(scope="module", params=["fastapi", "binary-format"])
def file_sdk(
    tmp_path_factory: pytest.TempPathFactory,
    file_app: FastAPI,
    request: pytest.FixtureRequest,
) -> Iterator[Path]:
    directory = tmp_path_factory.mktemp("file-sdk")
    document = export_contracts(file_app, kinds=("openapi",)).openapi
    serialized = json.dumps(document)
    if request.param == "binary-format":
        serialized = serialized.replace(
            '"contentMediaType": "application/octet-stream"', '"format": "binary"'
        )
    (directory / "openapi.json").write_text(serialized, encoding="utf-8")
    configuration = [
        '[codegen]\nversion = 1\n[codegen.sources.api]\nkind = "openapi"\ninput = "openapi.json"'
    ]
    for backend in ("httpx", "httpx2"):
        configuration.append(
            f'[codegen.targets.{backend}]\nlanguage = "python"\noutput = "files_{backend}"\n'
            f'package = "files_{backend}"\nhttp-client = "{backend}"'
        )
    configuration.append(
        '[codegen.targets.browser]\nlanguage = "typescript"\noutput = "browser"'
    )
    (directory / "fastapi-canon.toml").write_text(
        "\n".join(configuration), encoding="utf-8"
    )
    command = [sys.executable, "-m", "fastapi_canon", "generate", "--format", "json"]
    generated = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, timeout=60
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    checked = subprocess.run(
        [*command, "--check"], cwd=directory, capture_output=True, text=True, timeout=60
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    sys.path.insert(0, str(directory))
    try:
        yield directory
    finally:
        sys.path.remove(str(directory))
        for name in tuple(sys.modules):
            if name.startswith(("files_httpx", "files_httpx2")):
                del sys.modules[name]


@pytest.mark.parametrize("backend", ["httpx", "httpx2"])
async def test_python_upload_and_binary_roundtrip(
    file_sdk: Path, file_app: FastAPI, backend: str
) -> None:
    sdk = importlib.import_module(f"files_{backend}")
    models = importlib.import_module(f"files_{backend}.models")
    handle = BytesIO(PAYLOAD)
    async with (
        asyncio.timeout(15),
        serve(file_app) as url,
        sdk.ApiClient(
            url.replace("ws:", "http:"), headers={"Content-Type": "application/json"}
        ) as client,
    ):
        result = await client.files.upload(
            document=sdk.Upload('résumé".bin', handle),
            attachments=[
                sdk.Upload("empty.bin", b""),
                sdk.Upload("second.pdf", PAYLOAD, "application/pdf"),
            ],
            title="Übertragung",
            tags=["one", "zwei"],
            enabled=False,
            count=0,
            note=None,
            thumbnail=None,
        )
        assert result == {
            "files": [
                {
                    "name": "résumé%22.bin",
                    "type": "application/octet-stream",
                    "hex": PAYLOAD.hex(),
                },
                {
                    "name": "empty.bin",
                    "type": "application/octet-stream",
                    "hex": "",
                },
                {
                    "name": "second.pdf",
                    "type": "application/pdf",
                    "hex": PAYLOAD.hex(),
                },
            ],
            "title": "Übertragung",
            "tags": ["one", "zwei"],
            "enabled": False,
            "count": 0,
            "note": None,
            "thumbnail": None,
        }
        assert await client.files.raw(body=PAYLOAD) == PAYLOAD
        assert await client.files.download() == PAYLOAD
        assert await client.files.raw(body=b"") == b""
        assert await client.files.mixed() == PAYLOAD
        assert (await client.files.mixed(mode="json")).name == "report.pdf"
        assert await client.files.mixed(mode="empty") is None
        metadata = models.Blob(name="report.pdf")
        assert await client.files.metadata(body=metadata) == metadata
        assert await client.files.optional() == {"file": None, "note": None}
        assert await client.files.optional(
            thumbnail=sdk.Upload("preview.png", PAYLOAD), note="present"
        ) == {"file": "preview.png", "note": "present"}
        with pytest.raises(sdk.TransportError):
            await client.files.slow(options=sdk.RequestOptions(timeout=0.05))
        assert not handle.closed
        with pytest.raises(sdk.UploadRejectedError) as rejected:
            await client.files.upload(
                document=sdk.Upload("x.bin", b"x"),
                attachments=[sdk.Upload("attachment.bin", b"")],
                title="reject",
                tags=["tag"],
                enabled=True,
                count=1,
            )
        assert rejected.value.problem.status == 413
    assert not handle.closed
    handle.close()


@pytest.fixture(scope="module")
def browser_files(file_sdk: Path) -> tuple[str, Path]:
    node = shutil.which("node")
    compiler = ROOT / "node_modules/typescript/bin/tsc"
    assert node is not None and compiler.is_file(), (
        "Install the locked Node dependencies"
    )
    usage = file_sdk / "usage.ts"
    usage.write_text(
        """
import { createClient, UploadRejectedError, type Blob as Metadata } from "./browser/index.js";
const payload = new Uint8Array([0, 255, 128, 67, 97, 110, 111, 110, 13, 10]);
export async function run(baseUrl: string) {
  const client = createClient({baseUrl, headers: {"Content-Type": "application/json"}});
  try {
    const params = {document: new File([payload], 'résumé".bin'),
      attachments: [new File([], "empty.bin"), new File([payload], "second.pdf", {type: "application/pdf"})],
      title: "Übertragung", tags: ["one", "zwei"], enabled: false, count: 0, note: null, thumbnail: null};
    const uploaded = await client.files.upload(params);
    const raw: Blob = await client.files.raw({body: new Blob([payload])});
    const download: Blob = await client.files.download();
    const mixed: Blob | Metadata | void = await client.files.mixed({mode: "json"});
    if (!mixed || mixed instanceof Blob || mixed.name !== "report.pdf") throw new Error("Invalid mixed result");
    if ((await client.files.metadata({body: mixed})).name !== "report.pdf") throw new Error("Invalid JSON metadata");
    const empty: Blob = await client.files.raw({body: new Blob([])});
    if (empty.size !== 0 || await client.files.mixed({mode: "empty"}) !== undefined) throw new Error("Invalid empty file");
    const omitted = await client.files.optional();
    const optional = await client.files.optional({thumbnail: new File([payload], "preview.png"), note: "present"});
    if (omitted.file !== null || omitted.note !== null || optional.file !== "preview.png" || optional.note !== "present") throw new Error("Invalid optional parts");
    const rejected = await client.files.upload.safe({...params, title: "reject"});
    let typedError = false;
    try { await client.files.upload({...params, title: "reject"}); }
    catch (error) { typedError = error instanceof UploadRejectedError; }
    const abort = new AbortController(); abort.abort();
    let cancelled = false;
    try { await client.files.raw({body: new Blob([payload])}, {signal: abort.signal}); }
    catch (error) { cancelled = error instanceof DOMException && error.name === "AbortError"; }
    let timedOut = false;
    try { await client.files.slow({timeoutMs: 50}); }
    catch (error) { timedOut = error instanceof DOMException && error.name === "TimeoutError"; }
    return {uploaded, raw: Array.from(new Uint8Array(await raw.arrayBuffer())),
      download: Array.from(new Uint8Array(await download.arrayBuffer())),
      type: download.type, declared: !rejected.ok && rejected.kind === "declared", typedError, cancelled, timedOut};
  } finally { await client.close(); }
}
""",
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [
            node,
            str(compiler),
            "--strict",
            "--exactOptionalPropertyTypes",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--target",
            "es2022",
            "--module",
            "commonjs",
            "--lib",
            "es2022,dom,esnext.disposable",
            "--outDir",
            str(file_sdk / "js"),
            str(usage),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    runner = file_sdk / "runner.cjs"
    runner.write_text(
        'require("./js/usage.js").run(process.argv[2]).then(value => console.log(JSON.stringify(value))).catch(error => {console.error(error); process.exitCode = 1;});',
        encoding="utf-8",
    )
    return node, runner


async def test_typescript_upload_and_binary_roundtrip(
    browser_files: tuple[str, Path], file_app: FastAPI
) -> None:
    node, runner = browser_files
    async with asyncio.timeout(20), serve(file_app) as url:
        result = await asyncio.to_thread(
            subprocess.run,
            [node, str(runner), url.replace("ws:", "http:")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["uploaded"]["files"] == [
        {
            "name": "résumé%22.bin",
            "type": "application/octet-stream",
            "hex": PAYLOAD.hex(),
        },
        {"name": "empty.bin", "type": "application/octet-stream", "hex": ""},
        {"name": "second.pdf", "type": "application/pdf", "hex": PAYLOAD.hex()},
    ]
    assert data["uploaded"]["tags"] == ["one", "zwei"]
    assert data["uploaded"]["enabled"] is False and data["uploaded"]["count"] == 0
    assert data["raw"] == list(PAYLOAD) and data["download"] == list(PAYLOAD)
    assert data["type"] == "application/pdf"
    assert (
        data["declared"]
        and data["typedError"]
        and data["cancelled"]
        and data["timedOut"]
    )


@pytest.mark.parametrize("backend", ["httpx", "httpx2"])
def test_python_file_signatures(file_sdk: Path, backend: str) -> None:
    usage = file_sdk / f"usage_{backend}.py"
    usage.write_text(
        f"from files_{backend} import ApiClient, Upload\n"
        f"from files_{backend}.models import Blob as Metadata\n"
        "from io import BytesIO\n"
        "async def use(client: ApiClient) -> None:\n"
        "    await client.files.upload(document=Upload('x.bin', BytesIO(b'x')),\n"
        "        attachments=[Upload('y.bin', b'y')], title='Files', tags=['one'],\n"
        "        enabled=False, count=0, note=None, thumbnail=None)\n"
        "    binary: bytes = await client.files.raw(body=b'x')\n"
        "    mixed: bytes | Metadata | None = await client.files.mixed()\n"
        "    await client.files.metadata(body=Metadata(name='report.pdf'))\n"
        "    await client.files.optional()\n"
        "    Upload('invalid.bin', 'text')  # type: ignore[arg-type]\n"
        "    await client.files.raw(body='text')  # type: ignore[arg-type]\n",
        encoding="utf-8",
    )
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--warn-unused-ignores",
            "--no-incremental",
            str(usage),
        ],
        cwd=file_sdk,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize(
    "unsupported",
    [
        "nested",
        "nested-array",
        "encoding",
        "style",
        "optional",
        "media",
        "conditional",
        "encoded",
    ],
)
def test_unsupported_file_contracts_fail_before_writing(
    tmp_path: Path, file_app: FastAPI, unsupported: str
) -> None:
    document = copy.deepcopy(export_contracts(file_app, kinds=("openapi",)).openapi)
    body = document["paths"]["/upload"]["post"]["requestBody"]
    content = body["content"]["multipart/form-data"]
    if unsupported == "nested":
        content["schema"] = {
            "type": "object",
            "properties": {"metadata": {"type": "object"}},
        }
    elif unsupported == "nested-array":
        content["schema"] = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                }
            },
        }
    elif unsupported == "conditional":
        content["schema"] = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "if": {"required": ["name"]},
            "then": {"required": ["extra"]},
        }
    elif unsupported == "encoded":
        content["schema"] = {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "format": "binary",
                    "contentEncoding": "base64",
                }
            },
        }
    elif unsupported == "encoding":
        content["encoding"] = {
            "document": {"headers": {"X-Part": {"schema": {"type": "string"}}}}
        }
    elif unsupported == "optional":
        body["required"] = False
    elif unsupported == "style":
        content["encoding"] = {"attachments": {"explode": False}}
    else:
        body["content"]["application/json"] = {"schema": {"type": "string"}}
    (tmp_path / "openapi.json").write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "fastapi-canon.toml").write_text(
        '[codegen]\nversion = 1\n[codegen.sources.api]\nkind = "openapi"\ninput = "openapi.json"\n'
        '[codegen.targets.python]\nlanguage = "python"\noutput = "python"\npackage = "sdk"\n'
        '[codegen.targets.browser]\nlanguage = "typescript"\noutput = "browser"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "fastapi_canon", "generate", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["diagnostics"][0]["code"] == "canon.codegen.E002"
    assert report["diagnostics"][0]["locations"][0]["operation"] == "files.upload"
    assert not (tmp_path / "python").exists() and not (tmp_path / "browser").exists()
