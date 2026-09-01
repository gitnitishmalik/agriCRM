"""
Walking the routes the application actually serves.

🔴 Why this module exists.

Three guarantees in this project are enforced by walking `app.routes` and
asserting something about every route: that none redirects on a trailing slash
(`test_writes`), that none is reachable before the second factor unless it is
named in `deps.PRE_MFA` (`test_mfa_boundary`), and that no `/admin` page is
reachable without `current_admin`. Each is a property of the *whole* surface,
which is the only way to state it — "did someone remove the dependency, and did
they say why" is not a question a handful of hand-picked cases can answer.

FastAPI 0.141 made `include_router` lazy. `app.routes` no longer holds the
included `APIRoute` objects; it holds `_IncludedRouter` placeholders that
resolve at request time. The placeholders have no `.path` and no `.methods`, so
every one of those walks silently stopped seeing anything: 90 routes served, 4
visible, three guards iterating over nothing and asserting `[] == []`. Nothing
failed. That is the worst shape a control can fail in, and it is the same shape
CLAUDE.md records for the Django permission class that existed and was attached
to nothing.

`effective_route_contexts()` is the supported way back to the real routes. It is
private API, which is exactly why it is wrapped here once rather than spelled
out in three test files: when it changes, one import breaks loudly instead of
three walks going quiet.

The objects yielded are `APIRoute` or `_EffectiveRouteContext`. Both carry
`path`, `methods`, `name`, `endpoint` and `dependant`, which is everything the
callers read.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

try:  # pragma: no cover - depends on the installed FastAPI
    from fastapi.routing import _IncludedRouter
except ImportError:  # pragma: no cover - FastAPI < 0.141 flattened eagerly
    _IncludedRouter = ()  # type: ignore[assignment]


def iter_routes(app: FastAPI) -> Iterator[Any]:
    """
    Every route the application serves, including those behind a lazily
    included router.

    🔴 Do not replace a call to this with `for route in app.routes`. That reads
    like the same thing and, on the FastAPI this project pins, silently visits
    about four per cent of the surface.
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
        elif _IncludedRouter and isinstance(route, _IncludedRouter):
            yield from route.effective_route_contexts()


def route_paths(app: FastAPI) -> set[str]:
    """Every served path. Used by the trailing-slash guard and its fix."""
    return {route.path for route in iter_routes(app)}


def register_slash_aliases(router: APIRouter) -> APIRouter:
    """
    Register every route under both its own path and the other slash form.

    🔴 The rule is that neither form redirects. FastAPI answers a trailing-slash
    mismatch with a 307 to an *absolute* URL on the backend origin; behind the
    dev proxy that is cross-origin, browsers drop `Authorization` across
    origins, and the retry arrives unauthenticated. In a log it reads like an
    expiring session. A 307 that *keeps* the header is still wrong, because it
    doubles every request.

    This was previously done by hand, one stacked decorator at a time, on the
    five routes somebody noticed. Twenty-four more were added afterwards and
    nobody noticed, because the test that walks every route had gone blind (see
    `iter_routes`). Doing it here means a new route cannot be added without it:
    the alias is not something to remember.

    The alias is `include_in_schema=False`, so the OpenAPI document — and the
    TypeScript client generated from it — still describes exactly one path per
    endpoint.
    """
    prefix = router.prefix
    existing = {getattr(r, "path", None) for r in router.routes}

    for route in list(router.routes):
        if not isinstance(route, APIRoute):
            continue

        # `route.path` already carries the router's prefix, while
        # `add_api_route` applies it again — so the alias has to be computed on
        # the *unprefixed* path or it lands at `/api/v1/auth/api/v1/auth/login`.
        local = (
            route.path[len(prefix) :] if prefix and route.path.startswith(prefix) else route.path
        )
        alternate = local[:-1] if local.endswith("/") else local + "/"

        # An empty full path is not a route, and an alias that already exists
        # (the hand-written ones) must not be registered twice.
        if not (prefix + alternate) or (prefix + alternate) in existing:
            continue
        existing.add(prefix + alternate)

        router.add_api_route(
            alternate,
            route.endpoint,
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            methods=list(route.methods),
            operation_id=None,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            include_in_schema=False,
            response_class=route.response_class,
            name=f"{route.name}_alias",
            callbacks=route.callbacks,
            openapi_extra=route.openapi_extra,
        )

    return router
