"""Separate a single-yield generator's entry from its suspended continuation."""

import ast
from dataclasses import dataclass


@dataclass
class _Phases:
    before: list[ast.stmt]
    after: list[ast.stmt]
    setup: list[ast.stmt]
    yielded: ast.expr | None
    contexts: list[ast.With | ast.AsyncWith]


def _phases(body: list[ast.stmt]) -> _Phases | None:
    """Keep handlers and finally blocks attached to the phase that executes them.

    Loops, delegation, and yields inside handlers need multiple continuations and
    deliberately remain unsupported. No user code is compiled or executed.
    """
    for index, node in enumerate(body):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield):
            resume = ast.copy_location(
                ast.Expr(value=ast.copy_location(ast.Yield(value=None), node.value)),
                node,
            )
            return _Phases(
                body[:index],
                [resume, *body[index + 1 :]],
                body[:index],
                node.value.value,
                [],
            )
        if isinstance(node, ast.Try):
            inner = _phases(node.body)
            if inner is None:
                if _has_yield(node):
                    return None
                continue
            handlers = [
                ast.copy_location(
                    ast.ExceptHandler(
                        type=h.type,
                        name=h.name,
                        body=[
                            ast.copy_location(
                                ast.Try(
                                    body=h.body,
                                    handlers=[],
                                    orelse=[],
                                    finalbody=node.finalbody,
                                ),
                                h,
                            )
                        ]
                        if node.finalbody
                        else h.body,
                    ),
                    h,
                )
                for h in node.handlers
            ]
            # An entry failure runs finally immediately; normal entry suspends
            # before finally. Explicit handlers retain Python's ordering.
            if node.finalbody:
                handlers.append(
                    ast.copy_location(
                        ast.ExceptHandler(
                            type=ast.Name(id="BaseException", ctx=ast.Load()),
                            name=None,
                            body=[
                                *node.finalbody,
                                ast.copy_location(
                                    ast.Raise(exc=None, cause=None), node
                                ),
                            ],
                        ),
                        node,
                    )
                )
            entry = (
                ast.copy_location(
                    ast.Try(
                        body=inner.before, handlers=handlers, orelse=[], finalbody=[]
                    ),
                    node,
                )
                if handlers and inner.before
                else None
            )
            after = ast.copy_location(
                ast.Try(
                    body=inner.after,
                    handlers=node.handlers,
                    orelse=node.orelse,
                    finalbody=node.finalbody,
                ),
                node,
            )
            return _Phases(
                [*body[:index], *([entry] if entry else inner.before)],
                [after, *body[index + 1 :]],
                [*body[:index], *inner.setup],
                inner.yielded,
                inner.contexts,
            )
        if isinstance(node, ast.With | ast.AsyncWith):
            inner = _phases(node.body)
            if inner:
                kind = type(node)
                before = ast.copy_location(
                    kind(
                        items=node.items,
                        body=inner.before,
                        type_comment=node.type_comment,
                    ),
                    node,
                )
                context_after = ast.copy_location(
                    kind(
                        items=node.items,
                        body=inner.after,
                        type_comment=node.type_comment,
                    ),
                    node,
                )
                return _Phases(
                    [*body[:index], before],
                    [context_after, *body[index + 1 :]],
                    [*body[:index], *inner.setup],
                    inner.yielded,
                    [node, *inner.contexts],
                )
        if _has_yield(node):
            return None
    return None


def _has_yield(node: ast.AST) -> bool:
    if isinstance(
        node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ):
        return False
    return isinstance(node, ast.Yield | ast.YieldFrom) or any(
        _has_yield(child) for child in ast.iter_child_nodes(node)
    )
