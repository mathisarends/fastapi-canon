import assert from "node:assert/strict";
import { test } from "node:test";
import { createClient, HttpError, isHttpError, ProblemError, TransportError } from "../../../examples/sse/agent_runs/generated/index.js";
import { consumeWithErrors, decodeValidationError } from "../../../examples/sse/agent_runs/errors.js";

type MissingProblem = {
  code: "missing";
  status: 404;
  title: "Not found";
  type: "urn:example:missing";
};

if (false) {
  // @ts-expect-error A generic argument does not replace body validation.
  isHttpError<MissingProblem>(new Error());
}

function isMissingProblem(value: unknown): value is MissingProblem {
  return typeof value === "object" && value !== null
    && "code" in value && value.code === "missing"
    && "status" in value && value.status === 404
    && "title" in value && value.title === "Not found"
    && "type" in value && value.type === "urn:example:missing";
}

test("HTTP error guards validate bodies before narrowing details", () => {
  const body: MissingProblem = {
    code: "missing", status: 404, title: "Not found", type: "urn:example:missing",
  };
  const error: unknown = new HttpError(404, body, new Response(null, { status: 404 }));
  if (!isHttpError(error, isMissingProblem)) assert.fail("Expected a validated problem");
  const code: "missing" = error.details.code;
  assert.equal(code, "missing");
  for (const details of ["<html>Bad gateway</html>", undefined, null, {}, { code: "missing" }, { ...body, status: 500 }]) {
    const unexpected = new HttpError(404, details, new Response(null, { status: 404 }));
    assert.equal(isHttpError(unexpected, isMissingProblem), false);
    assert.ok(unexpected instanceof HttpError);
    assert.equal(unexpected.details, details);
  }
  for (const other of [body, new Error("network"), new TransportError("network"), null]) {
    assert.equal(isHttpError(other, isMissingProblem), false);
  }
});

test("HTTP error guards do not invoke body validators for non-HTTP failures", () => {
  let calls = 0;
  const validator = (_value: unknown): _value is MissingProblem => { calls++; return false; };
  assert.equal(isHttpError(new TransportError("offline"), validator), false);
  assert.equal(calls, 0);
});

test("body guards leave status and operation checks to callers", () => {
  const body = { code: "missing", status: 404, title: "Not found", type: "urn:example:missing" };
  const error = new HttpError(502, body, new Response(null, { status: 502 }));
  assert.equal(isHttpError(error, isMissingProblem), true);
  assert.equal(error.status, 502);
});

const validation = {
  type: "urn:fastapi-canon:problem/request_validation_error",
  title: "Request validation failed",
  code: "request_validation_error",
  status: 422,
  errors: [{ code: "greater_than_equal", detail: "Input should be >= 0", in: "query", parameter: "delay" }],
};

test("consumer example handles validation problems and rethrows other failures", async t => {
  t.mock.method(console, "error", () => {});
  const makeClient = (body: unknown, contentType: string) => createClient({
    baseUrl: "http://example.test",
    fetch: async () => new Response(JSON.stringify(body), { status: 422, headers: { "Content-Type": contentType } }),
  });
  await consumeWithErrors(makeClient(validation, "application/problem+json"), "demo");
  const other = { ...validation, code: "other_problem", errors: undefined };
  await assert.rejects(() => consumeWithErrors(makeClient(other, "application/problem+json"), "demo"), ProblemError);
  for (const unexpected of [[], "<html>Bad gateway</html>", null, validation]) {
    await assert.rejects(() => consumeWithErrors(makeClient(unexpected, "application/json"), "demo"), HttpError);
  }
});

test("consumer decoder accepts only well-formed validation problems", () => {
  assert.equal(decodeValidationError(new ProblemError(validation)), validation);
  for (const problem of [
    { ...validation, code: "other_problem" },
    { ...validation, status: 400 },
    { ...validation, errors: [{}] },
    { ...validation, errors: "invalid" },
  ]) {
    assert.equal(decodeValidationError(new ProblemError(problem)), undefined);
  }
  const response = new Response(null, { status: 422 });
  assert.equal(decodeValidationError(new HttpError(422, validation, response)), undefined);
  assert.equal(decodeValidationError(new TransportError("offline")), undefined);
});
