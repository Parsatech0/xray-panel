"""Public subscription URLs (Phase 6).

GET /sub/{token}  — anonymous route returning a v2rayNG/Nekoray-style
    subscription: base64 of the client's share links (one per line).
GET /sub/{token}/view — same links, rendered as plain readable HTML.

The token is an unguessable URL-safe secret generated on first use, so it
acts as the auth credential itself; the response only ever exposes *this*
token's client links, nothing else about the panel. Unknown tokens -> 404.
"""

import base64

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import app.crud as crud
import app.models as models
from app.database import get_db
from app.routers.helpers import csrf_from_session
from app.share_links import build_share_link, make_qr_svg_data_uri

router = APIRouter()

templates = Jinja2Templates(directory="templates")

SUB_ROUTE = "/sub"


def ensure_token(db: Session, client: models.Client) -> str:
    """Token for the client (created lazily, persisted)."""
    return crud.ensure_sub_token(db, client)


def request_origin(request: Request) -> tuple[str, str]:
    """(scheme, host) for building absolute URLs (proxy-aware)."""
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", "")
    return scheme, host


def subscription_url(request: Request, db: Session, client: models.Client) -> str:
    """Absolute subscription URL for the client, e.g. http://h/sub/tok."""
    scheme, host = request_origin(request)
    token = ensure_token(db, client)
    return f"{scheme}://{host}{SUB_ROUTE}/{token}"


def _links_for_host(client: models.Client, host: str) -> list[str]:
    """Shareable links for this client (empty when host is unknown)."""
    links: list[str] = []
    main = build_share_link(client.inbound, client, host)
    if main:
        links.append(main)
    return links


@router.get("/sub/{token}")
def get_subscription(token: str, request: Request, db: Session = Depends(get_db)):
    client = crud.get_client_by_sub_token(db, token)
    if not client:
        raise HTTPException(status_code=404, detail="subscription not found")

    _, host = request_origin(request)
    links = _links_for_host(client, host)
    if not links:
        raise HTTPException(status_code=404, detail="no link available")

    return PlainTextResponse(
        content=base64.b64encode("\n".join(links).encode("utf-8")).decode("ascii"),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/sub/{token}/view", response_class=HTMLResponse)
def view_subscription(token: str, request: Request, db: Session = Depends(get_db)):
    client = crud.get_client_by_sub_token(db, token)
    if not client:
        raise HTTPException(status_code=404, detail="subscription not found")

    scheme, host = request_origin(request)
    links = _links_for_host(client, host)
    sub_link = f"{scheme}://{host}{SUB_ROUTE}/{client.sub_token}"
    qr = make_qr_svg_data_uri(sub_link)
    return templates.TemplateResponse(
        "subscription.html",
        {
            "request": request,
            "client": client,
            "links": links,
            "sub_link": sub_link,
            "qr": qr,
            "csrf_token": csrf_from_session(request),
        },
    )
