"""Drive connection portal; only the deployment owner can initiate or resume OAuth."""

import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.dependencies import get_personal_context
from app.core.request_limits import RequestBodyLimitMiddleware
from app.services.capture_repository import CaptureRepository
from app.services.drive_oauth import SESSION_SECONDS, DriveOAuth, OAuthConflict, OAuthStateError

settings = get_settings()
router = APIRouter(prefix="/oauth/google", tags=["Drive connection"])
COOKIE_NAME = "voice_notion_drive_session"
COOKIE_PATH = "/oauth/google/callback"


class OAuthOwnerGuard:
    """Authenticate owner operations before JSON parsing; keep OAuth responses private."""

    def __init__(self, app, api_key: str, redirect_uri: str):
        self.app, self.api_key = app, api_key
        url = urlsplit(redirect_uri)
        self.origin = f"{url.scheme}://{url.netloc}"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/oauth/google/"):
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                ]
            await send(message)

        protected = scope["path"] in (
            "/oauth/google/start",
            "/oauth/google/finish",
            "/oauth/google/status",
        )
        if protected:
            headers = dict(scope.get("headers", []))
            key = headers.get(b"x-api-key", b"")
            if (
                not self.api_key
                or not key
                or not secrets.compare_digest(key, self.api_key.encode())
            ):
                return await JSONResponse({"detail": "Personal API key required"}, 401)(
                    scope, receive, private_send
                )
            origin = headers.get(b"origin")
            if origin is not None and origin != self.origin.encode():
                return await JSONResponse({"detail": "Use the configured connection origin"}, 403)(
                    scope, receive, private_send
                )
            return await RequestBodyLimitMiddleware(self.app, 4096)(scope, receive, private_send)
        return await self.app(scope, receive, private_send)


class StartInput(BaseModel):
    root_name: str = Field(default="Voice-Notion-UAT", min_length=1, max_length=100)


def service():
    return DriveOAuth(CaptureRepository(settings.CAPTURE_DATA_DIR), settings)


def execute(operation):
    try:
        return operation()
    except OAuthStateError:
        return JSONResponse(
            {"detail": "Authorization session invalid, expired or cancelled; start again"}, 400
        )
    except OAuthConflict:
        return JSONResponse(
            {
                "detail": "Connection conflict; retain the same client, account and root, or finish pending setup"
            },
            409,
        )
    except Exception:
        # Never return provider errors, authorization codes, tokens or client secrets.
        return JSONResponse(
            {"detail": "Connection incomplete; check configuration or retry pending setup"}, 502
        )


@router.get("/setup", response_class=HTMLResponse)
def setup():
    nonce = secrets.token_urlsafe(24)
    page = (Path(__file__).parent.parent / "static" / "drive_setup.html").read_text()
    response = HTMLResponse(page.replace("{{nonce}}", nonce))
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@router.post("/start", dependencies=[Depends(get_personal_context)])
def start(payload: StartInput):
    result = execute(lambda: service().start(payload.root_name.strip()))
    if isinstance(result, JSONResponse):
        return result
    url, browser = result
    response = JSONResponse({"authorization_url": url})
    response.set_cookie(
        COOKIE_NAME,
        browser,
        max_age=SESSION_SECONDS,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.DRIVE_OAUTH_REDIRECT_URI.startswith("https:"),
        samesite="lax",
    )
    return response


@router.get("/callback")
def callback(request: Request):
    query = request.query_params
    if (
        len(request.scope["query_string"]) > 8192
        or len(query.getlist("state")) != 1
        or len(query.getlist("code")) > 1
        or not request.cookies.get(COOKIE_NAME)
    ):
        result = JSONResponse({"detail": "Invalid authorization response"}, 400)
    else:
        result = execute(
            lambda: service().callback(
                query.get("state", ""),
                request.cookies.get(COOKIE_NAME, ""),
                query.get("code", ""),
                denied="error" in query,
            )
        )
    if not isinstance(result, JSONResponse):
        # Leave the callback URL so the final page contains no authorization code or state.
        result = RedirectResponse("/oauth/google/complete", status_code=303)
    result.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        secure=settings.DRIVE_OAUTH_REDIRECT_URI.startswith("https:"),
        httponly=True,
        samesite="lax",
    )
    return result


@router.get("/complete", response_class=HTMLResponse)
def complete():
    return HTMLResponse(
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Drive 授權流程</title><h1>Google 授權回覆已處理</h1>"
        '<p>請回到測試流程，或到<a href="/oauth/google/setup">連線頁</a>確認連接狀態。</p></html>',
        headers={
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        },
    )


@router.get("/status", dependencies=[Depends(get_personal_context)])
def status():
    return execute(lambda: service().status())


@router.post("/finish", dependencies=[Depends(get_personal_context)])
def finish():
    return execute(lambda: service().finish())
