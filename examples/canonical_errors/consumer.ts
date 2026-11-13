import { ProjectsClient } from "./dist/rpc/typescript/index.js";
import { ProblemError as RpcProblemError } from "./dist/rpc/typescript/problem.js";
import {
  createClient,
  ProblemError as StreamProblemError,
} from "./dist/streams/typescript/index.js";

export interface PublicProblem {
  readonly type: string;
  readonly code: string;
  readonly title: string;
  readonly status?: number;
  readonly [extension: string]: unknown;
}

/** Recognize each client's own class; compare public problems by their type URI. */
export function problemOf(error: unknown): PublicProblem | null {
  if (error instanceof RpcProblemError || error instanceof StreamProblemError) {
    return error.problem;
  }
  return null;
}

export async function readEvents(
  baseUrl: string,
  projectId: number,
  signal?: AbortSignal,
): Promise<number[]> {
  const streams = createClient({ baseUrl, credentials: "include" });
  const values: number[] = [];
  for await (const event of streams.projects.events({ projectId }, { signal })) {
    values.push(event.sequence);
  }
  return values;
}

/** Each call returns an owned connection; the caller closes it in finally. */
export async function openProjectsRpc(
  baseUrl: string,
  options: {
    readonly signal?: AbortSignal;
    readonly shared?: boolean;
    readonly refreshSession: () => Promise<void>;
  },
): Promise<ProjectsClient> {
  const socketBase = new URL(baseUrl);
  socketBase.protocol = socketBase.protocol === "https:" ? "wss:" : "ws:";
  let credentialsExpired = false;
  const connect = () => ProjectsClient.connect({
    baseUrl: socketBase.href.replace(/\/+$/, ""),
    lazy: false,
    signal: options.signal,
    reconnect: options.shared ?? false,
    retryAttempts: 0,
    notificationOverflow: "close",
    credentials: async (_endpoint, signal) => {
      signal?.throwIfAborted();
      if (credentialsExpired) {
        await options.refreshSession();
        signal?.throwIfAborted();
        credentialsExpired = false;
      }
      return {};
    },
    socketFactory: (url, protocols) => {
      const socket = new WebSocket(url, protocols ? [...protocols] : undefined);
      socket.addEventListener("close", (event) => {
        if (event.code === 1008 && event.reason === "credentials_expired") {
          credentialsExpired = true;
        }
      });
      return socket;
    },
  });
  options.signal?.throwIfAborted();
  try {
    return await connect();
  } catch (error) {
    options.signal?.throwIfAborted();
    // Browsers hide handshake status; renew once before retrying initial admission.
    await options.refreshSession();
    options.signal?.throwIfAborted();
    return await connect();
  }
}
