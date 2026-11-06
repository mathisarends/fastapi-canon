import type * as Models from "./models.js";
import { Runtime, type ClientOptions, type RequestOptions } from "./internal/runtime.js";
import { createClient as apiFactory } from "./internal/api_sse/client.js";
import { BindingClient as controlClient } from "./internal/control_jsonrpc/client.js";

export interface Options extends ClientOptions { bindings?: { control?: { client?: controlClient; owned?: boolean; connect?: Parameters<typeof controlClient.connect>[0] }; } }
export class ApiClient {
  private readonly runtime: Runtime;
  constructor(options: Options) { this.runtime = new Runtime(options); this.runtime.register({"api:sse": { create: () => apiFactory(this.runtime.httpOptions()), owned: true },"control": { create: () => controlClient.connect({lazy: true, baseUrl: this.runtime.websocketUrl("").replace(/\/$/, ""), ...options.bindings?.control?.connect}), client: options.bindings?.control?.client, owned: options.bindings?.control?.owned ?? !options.bindings?.control?.client },}); }
  close(): Promise<void> { return this.runtime.close(); }
  readonly projects = {
cancel : (params: { projectId: Models.ProjectsCancelProjectId }, options?: RequestOptions): Promise<void> => this.runtime.call(async requestOptions => (await this.runtime.binding<controlClient>("control"))["projects"]["cancel"]({ "projectId": params.projectId }, requestOptions), options),
events : (params: { projectId: Models.ProjectsEventsProjectId }, options?: RequestOptions): AsyncIterable<Models.ProjectsEventsResult> => this.runtime.stream(async () => (await this.runtime.binding<ReturnType<typeof apiFactory>>("api:sse"))["projects"]["events"]({ "projectId": params.projectId }, options), options?.signal),
get : (params: { projectId: Models.ProjectsGetProjectId }, options?: RequestOptions): Promise<Models.ProjectsGetResult> => this.runtime.request<Models.ProjectsGetResult>({"path": "/projects/{project_id}", "method": "GET", "statuses": ["200"], "bodyless": [], "parameters": [{"name": "projectId", "wire": "project_id", "location": "path", "required": true}]}, params, options),
};
}
export function createClient(options: Options): ApiClient { return new ApiClient(options); }
