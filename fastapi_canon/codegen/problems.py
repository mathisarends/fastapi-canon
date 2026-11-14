from importlib.resources import files

from fastapi_canon.codegen.generators import ClientLanguage


def problem_runtime(language: ClientLanguage) -> str:
    name = "problem.py" if language == "python" else "problem.ts"
    return (
        files("fastapi_canon.codegen")
        .joinpath("templates", name)
        .read_text(encoding="utf-8")
    )
