from __future__ import annotations
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any
from . import models as Models
from .internal.runtime import Runtime, RequestOptions, UNSET, UnsetType
from .internal.api_sse import runtime as apiSse
from .internal.control_jsonrpc.client import BindingClient as controlClient

class ProjectsApi:
    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
    async def cancel(self, *, project_id: Models.ProjectsCancelProjectId, options: RequestOptions | None = None) -> Models.ProjectsCancelResult:
        return await self._runtime.call('control', ('projects', 'cancel'), {'project_id': project_id}, options)
    async def events(self, *, project_id: Models.ProjectsEventsProjectId, options: RequestOptions | None = None) -> AsyncIterator[Models.ProjectsEventsResult]:
        stream = await self._runtime.open_stream('api:sse', {'path': '/projects/{project_id}/events', 'method': 'GET', 'canon_stream': True, 'parameters': [{'wire': 'project_id', 'location': 'path', 'required': True}]}, {'project_id': project_id}, Models.ProjectsEventsResult, {'project_id': Models.ProjectsEventsProjectId}, options, apiSse.RequestOptions)
        async with self._runtime.iterating(stream) as stream:
            async for item in stream:
                yield self._runtime.result(item, Models.ProjectsEventsResult)
    async def get(self, *, project_id: Models.ProjectsGetProjectId, options: RequestOptions | None = None) -> Models.ProjectsGetResult:
        return await self._runtime.request({'path': '/projects/{project_id}', 'method': 'GET', 'statuses': ['200'], 'bodyless': [], 'parameters': [{'wire': 'project_id', 'location': 'path', 'required': True}]}, {'project_id': project_id}, {'200': Models.ProjectsGetResponse200}, options, {'project_id': Models.ProjectsGetProjectId})

class ApiClient:
    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None, http_client: Any = None, bindings: dict[str, dict[str, Any]] | None = None, clients: dict[str, Any] | None = None) -> None:
        self._runtime = Runtime(base_url, headers=headers, http_client=http_client)
        self._runtime.register('api:sse', lambda: apiSse.Transport(base_url, http_client=self._runtime.http), owned=True)
        self._runtime.register('control', lambda: controlClient.connect(**{'lazy': True, **{'base_url': self._runtime.websocket_url('').rstrip('/')}, **(bindings or {}).get('control', {})}), client=(clients or {}).get('control'), owned='control' not in (clients or {}))
        self.projects = ProjectsApi(self._runtime)
    async def aclose(self) -> None:
        await self._runtime.aclose()
    async def __aenter__(self) -> ApiClient:
        return self
    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
