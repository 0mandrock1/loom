"""Browser binding for personal provider consent; account authority lives in knowledge."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from server.auth.bearer import missing_bearer, verify_bearer_header
from server.auth.oauth_store import PENDING_TTL, token_id
from server.knowledge.app import connected_accounts as accounts

router = APIRouter()


def _cookie(state: str) -> str:
    return "loom_connection_" + token_id(state)[:16]


@router.post("/v1/connect/{kind}/start")
async def start(kind: str, request: Request) -> JSONResponse:
    auth = verify_bearer_header(request.headers.get("Authorization"))
    if auth is None:
        raise missing_bearer()
    # This route never applies an on-behalf header, including from a trusted caller.
    if request.headers.get("X-Loom-On-Behalf-Of"):
        raise accounts.unavailable()
    url, state, binding = await accounts.start(auth, kind)
    response = JSONResponse({"authorization_url": url}, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        _cookie(state),
        binding,
        max_age=PENDING_TTL,
        path=f"/v1/connect/{kind}/callback",
        secure=accounts.client(kind).redirect_uri.startswith("https://"),
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/v1/connect/{kind}/callback")
async def callback(kind: str, request: Request) -> JSONResponse:
    query = request.query_params
    if any(len(query.getlist(key)) > 1 for key in ("state", "code", "error")):
        raise accounts.unavailable()
    state = query.get("state", "")
    await accounts.callback(
        kind,
        state,
        request.cookies.get(_cookie(state), ""),
        query.get("code", ""),
        denied=bool(query.get("error")),
    )
    response = JSONResponse({"connected": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(_cookie(state), path=f"/v1/connect/{kind}/callback")
    return response


def attach(app: FastAPI) -> None:
    app.include_router(router)
