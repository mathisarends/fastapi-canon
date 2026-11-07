import sys
from typing import cast

from examples.sse.agent_runs.generated_python import (
    Client,
    HttpError,
    Problem,
    ProblemError,
    StreamParseError,
    TransportError,
)
from examples.sse.agent_runs.generated_python.models import RequestValidationProblem
from pydantic import ValidationError


async def consume_with_errors(client: Client, run_id: str) -> None:
    try:
        async for event in client.agent_runs.stream_run_events(run_id=run_id):
            if event.type == "run.failed":
                print(event.message)
            else:
                print(event)
    except ProblemError:
        # Canon reports rejected requests and stream failures as problem details.
        # The generated error is generic; this client raises the shared Problem model.
        error = cast(ProblemError[Problem], sys.exception())
        if error.problem.code == "request_validation_error":
            try:
                problem = RequestValidationProblem.model_validate(
                    error.problem.model_dump(exclude_unset=True)
                )
            except ValidationError:
                pass
            else:
                print("Invalid stream parameters", [e.detail for e in problem.errors])
                return
        print("Request failed", error.problem.code, error.problem.title)
        raise
    except HttpError as error:
        # Responses that are not problem details, for example from a proxy.
        print("Unexpected HTTP response", error.status, error.details)
        raise
    except StreamParseError as error:
        print("Invalid event data", error.operation_id, error.category)
        raise
    except TransportError as error:
        print("Stream transport failed", str(error))
        raise
