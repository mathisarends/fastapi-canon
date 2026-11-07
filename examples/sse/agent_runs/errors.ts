import {
  HttpError, ProblemError, StreamParseError, TransportError,
  type Client, type RequestOptions, type RequestValidationProblem,
} from "./generated/index.js";

export async function consumeWithErrors(
  client: Client,
  runId: string,
  options: RequestOptions = {},
): Promise<void> {
  try {
    for await (const event of client.agentRuns.streamRunEvents({ runId }, options)) {
      if (event.type === "run.failed") console.error(event.message);
      else console.log(event);
    }
  } catch (error: unknown) {
    if (options.signal?.aborted) throw error;
    const problem = decodeValidationError(error);
    if (problem !== undefined) {
      console.error("Invalid stream parameters", problem.errors.map(item => item.detail));
      return;
    }
    if (error instanceof ProblemError) {
      // Canon reports rejected requests and stream failures as problem details.
      console.error("Request failed", error.problem.code, error.problem.title);
    } else if (error instanceof HttpError) {
      // Responses that are not problem details, for example from a proxy.
      console.error("Unexpected HTTP response", error.status, error.details);
    } else if (error instanceof StreamParseError) {
      console.error("Invalid event JSON", error.data);
    } else if (error instanceof TransportError) {
      console.error("Stream transport failed", error.message);
    }
    throw error;
  }
}

// Consumer-local decoder: check the problem code and shape before using its fields.
export function decodeValidationError(error: unknown): RequestValidationProblem | undefined {
  if (!(error instanceof ProblemError)) return undefined;
  const problem: unknown = error.problem;
  return isValidationProblem(problem) ? problem : undefined;
}

function isValidationProblem(value: unknown): value is RequestValidationProblem {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  if (!("code" in value) || value.code !== "request_validation_error") return false;
  if (!("status" in value) || value.status !== 422) return false;
  return "errors" in value && Array.isArray(value.errors) && value.errors.every((item: unknown) => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) return false;
    return "code" in item && typeof item.code === "string"
      && "detail" in item && typeof item.detail === "string";
  });
}
