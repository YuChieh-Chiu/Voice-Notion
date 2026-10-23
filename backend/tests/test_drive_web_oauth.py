import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from drive_fakes import DriveCall, FakeDrive
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.routes import drive_oauth as routes
from app.services import drive_storage
from app.services.capture_repository import CaptureRepository
from app.services.drive_oauth import DRIVE_SCOPE, DriveOAuth, OAuthConflict

EXCHANGE = DriveOAuth.exchange
HEADERS = {"X-API-Key": "test-personal"}


class OAuthDrive(FakeDrive):
    account_id = "test-account"

    def about(self):
        return SimpleNamespace(
            get=lambda **kwargs: DriveCall(
                lambda: {
                    "user": {"permissionId": self.account_id, "emailAddress": "uat@example.invalid"}
                }
            )
        )


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        CAPTURE_DATA_DIR=tmp_path,
        CAPTURE_DESTINATION="drive",
        DRIVE_CLIENT_ID="test-client",
        DRIVE_CLIENT_SECRET="test-secret",
        DRIVE_OAUTH_REDIRECT_URI="http://localhost:8081/oauth/google/callback",
    )
    repository = CaptureRepository(tmp_path)
    service = DriveOAuth(repository, settings)
    fake = OAuthDrive()
    exchange = MagicMock(
        return_value={"access_token": "test-access", "refresh_token": "test-refresh"}
    )
    monkeypatch.setattr(DriveOAuth, "exchange", exchange)
    monkeypatch.setattr(DriveOAuth, "service", lambda *args, **kwargs: fake)
    monkeypatch.setattr(routes, "settings", settings)
    app = FastAPI()
    app.add_middleware(
        routes.OAuthOwnerGuard,
        api_key="test-personal",
        redirect_uri=settings.DRIVE_OAUTH_REDIRECT_URI,
    )
    app.include_router(routes.router)
    http = TestClient(app, base_url="http://localhost:8081")
    return http, service, fake, exchange


def start(http):
    response = http.post(
        "/oauth/google/start", headers=HEADERS, json={"root_name": "Voice-Notion-UAT"}
    )
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return query, http.cookies.get(routes.COOKIE_NAME), response


def complete(http, query):
    return http.get(
        "/oauth/google/callback?" + urlencode({"state": query["state"][0], "code": "test-code"})
    )


def test_owner_guard_precedes_json_parser_and_blocks_cross_origin(oauth):
    http, _, _, exchange = oauth
    for path in ("start", "finish", "status"):
        response = http.request(
            "GET" if path == "status" else "POST",
            "/oauth/google/" + path,
            content=b"not valid JSON",
        )
        assert response.status_code == 401
    assert (
        http.post(
            "/oauth/google/start", headers={**HEADERS, "Origin": "https://evil.invalid"}, json={}
        ).status_code
        == 403
    )
    assert http.post("/oauth/google/start", headers=HEADERS, content=b"x" * 4097).status_code == 413
    exchange.assert_not_called()


@pytest.mark.parametrize("path", ["/oauth/google/setup", "/oauth/google/complete"])
def test_portal_uses_local_assets_and_private_responses(oauth, path):
    http, _, _, _ = oauth
    response = http.get(path)
    assert response.status_code == 200
    assert "cdn." not in response.text and "localStorage" not in response.text
    assert "nonce-" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


def test_bound_pkce_authorization_is_encrypted_and_available_to_worker(oauth, monkeypatch):
    http, service, fake, exchange = oauth
    query, cookie, response = start(http)
    assert query["redirect_uri"] == [service.settings.DRIVE_OAUTH_REDIRECT_URI]
    assert query["scope"] == [DRIVE_SCOPE] and query["code_challenge_method"] == ["S256"]
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=lax" in response.headers["set-cookie"]
    )
    assert query["state"][0].encode() not in service.repository.database.read_bytes()
    assert cookie.encode() not in service.repository.database.read_bytes()
    assert complete(http, query).status_code == 200
    verifier = exchange.call_args.args[1]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert query["code_challenge"] == [challenge]
    assert b"test-refresh" not in service.repository.database.read_bytes()
    reopened = DriveOAuth(CaptureRepository(service.settings.CAPTURE_DATA_DIR), service.settings)
    assert reopened.status()["connected"]
    monkeypatch.setattr(drive_storage, "build", lambda *args, **kwargs: fake)
    storage = drive_storage.DriveStorage(service.repository, service.settings)
    assert storage.settings.DRIVE_REFRESH_TOKEN == "test-refresh"
    assert storage.settings.DRIVE_FOLDER_ID == reopened.status()["folder_id"]
    assert service.settings.DRIVE_FOLDER_ID == ""


def test_stolen_state_and_wrong_cookie_do_not_consume_owner_session(oauth):
    http, service, _, exchange = oauth
    query, cookie, _ = start(http)
    http.cookies.clear()
    assert complete(http, query).status_code == 400
    http.cookies.set(routes.COOKIE_NAME, "wrong-cookie", path=routes.COOKIE_PATH)
    assert complete(http, query).status_code == 400
    exchange.assert_not_called()
    http.cookies.set(routes.COOKIE_NAME, cookie, path=routes.COOKIE_PATH)
    assert complete(http, query).status_code == 200
    assert service.status()["connected"]


@pytest.mark.parametrize("case", ["expired", "denied", "replayed"])
def test_expired_denied_and_replayed_callbacks_fail_closed(oauth, case):
    http, service, fake, exchange = oauth
    query, cookie, _ = start(http)
    if case == "expired":
        with service.repository.connection() as conn:
            conn.execute("UPDATE drive_oauth_sessions SET expires=0")
    elif case == "denied":
        response = http.get(
            "/oauth/google/callback", params={"state": query["state"][0], "error": "access_denied"}
        )
        assert response.status_code == 400
    else:
        assert complete(http, query).status_code == 200
    count = len(fake.operations)
    http.cookies.set(routes.COOKIE_NAME, cookie, path=routes.COOKIE_PATH)
    assert complete(http, query).status_code == 400
    assert len(fake.operations) == count
    assert exchange.call_count == (1 if case == "replayed" else 0)


def test_lost_folder_create_response_resumes_same_root(oauth, monkeypatch):
    http, service, fake, _ = oauth
    original = fake.create

    def lost_response(**kwargs):
        call = original(**kwargs)

        def execute(**ignored):
            call.execute()
            raise ConnectionError("test response lost")

        return SimpleNamespace(execute=execute)

    monkeypatch.setattr(fake, "create", lost_response)
    query, _, _ = start(http)
    assert complete(http, query).status_code == 502
    pending = service.status()
    assert pending["pending"] and not pending["connected"]
    with pytest.raises(ValueError):
        service.worker_settings()
    result = http.post("/oauth/google/finish", headers=HEADERS)
    assert result.status_code == 200 and result.json()["connected"]
    assert result.json()["folder_id"] == pending["folder_id"]
    assert len([op for op in fake.operations if op[0] == "folder"]) == 1


def test_reconnect_keeps_root_and_rejects_different_account(oauth):
    http, service, fake, _ = oauth
    query, _, _ = start(http)
    assert complete(http, query).status_code == 200
    root = service.status()["folder_id"]
    query, _, _ = start(http)
    assert complete(http, query).status_code == 200
    assert service.status()["folder_id"] == root
    fake.account_id = "other-account"
    query, _, _ = start(http)
    assert complete(http, query).status_code == 409
    assert service.status()["connected"] and service.status()["folder_id"] == root


def test_pending_connection_with_revoked_token_can_reauthorize_same_root(oauth, monkeypatch):
    http, service, fake, exchange = oauth
    original_create = fake.create
    monkeypatch.setattr(
        fake,
        "create",
        lambda **kwargs: SimpleNamespace(
            execute=MagicMock(side_effect=ConnectionError("test temporary failure"))
        ),
    )
    query, _, _ = start(http)
    assert complete(http, query).status_code == 502
    before, _ = service.connection()
    monkeypatch.setattr(fake, "create", original_create)
    exchange.return_value = {"access_token": "test-new-access", "refresh_token": "test-new-refresh"}
    query, _, _ = start(http)
    assert complete(http, query).status_code == 200
    after, status = service.connection()
    assert status == "active" and after["refresh_token"] == "test-new-refresh"
    assert (after["folder_id"], after["account_id"], after["root_marker"]) == (
        before["folder_id"],
        before["account_id"],
        before["root_marker"],
    )


def test_client_changes_and_manual_configuration_require_migration(oauth, monkeypatch):
    http, service, _, _ = oauth
    monkeypatch.setattr(service.settings, "DRIVE_FOLDER_ID", "legacy-root")
    assert http.post("/oauth/google/start", headers=HEADERS, json={}).status_code == 409
    monkeypatch.setattr(service.settings, "DRIVE_FOLDER_ID", "")
    query, _, _ = start(http)
    assert complete(http, query).status_code == 200
    monkeypatch.setattr(service.settings, "DRIVE_CLIENT_ID", "another-client")
    with pytest.raises(OAuthConflict):
        service.worker_settings()


def test_provider_failures_do_not_echo_codes_or_secrets(oauth):
    http, _, _, exchange = oauth
    exchange.side_effect = RuntimeError("test-secret test-code test-refresh")
    query, _, _ = start(http)
    response = complete(http, query)
    assert response.status_code == 502
    assert all(value not in response.text for value in ("test-secret", "test-code", "test-refresh"))


@pytest.mark.parametrize(
    "scope", [DRIVE_SCOPE, DRIVE_SCOPE + " https://www.googleapis.com/auth/drive"]
)
def test_token_exchange_uses_fixed_endpoint_and_exact_scope(oauth, monkeypatch, scope):
    _, service, _, _ = oauth
    client_type = httpx.Client

    def reply(request):
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        assert b"code_verifier=test-verifier" in request.content
        return httpx.Response(
            200,
            json={"access_token": "test-access", "refresh_token": "test-refresh", "scope": scope},
        )

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return client_type(**kwargs, transport=httpx.MockTransport(reply))

    monkeypatch.setattr(httpx, "Client", client)
    if scope == DRIVE_SCOPE:
        assert EXCHANGE(service, "test-code", "test-verifier")["refresh_token"] == "test-refresh"
    else:
        with pytest.raises(ValueError):
            EXCHANGE(service, "test-code", "test-verifier")


@pytest.mark.parametrize(
    "uri",
    [
        "http://example.org/oauth/google/callback",
        "https://example.org/wrong",
        "https://example.org/oauth/google/callback?next=evil",
        "https://user@example.org/oauth/google/callback",
    ],
)
def test_callback_configuration_rejects_unsafe_redirects(uri):
    with pytest.raises(ValueError):
        Settings(_env_file=None, DRIVE_OAUTH_REDIRECT_URI=uri)
