"""Owner-only Web OAuth with browser binding and encrypted, shared Drive credentials."""

import base64
import fcntl
import hashlib
import json
import os
import secrets
import time
from contextlib import contextmanager
from urllib.parse import urlencode

import httplib2
import httpx
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import Settings
from app.services.capture_repository import CaptureRepository

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SESSION_SECONDS = 600
FOLDER_MIME = "application/vnd.google-apps.folder"
FOLDER_FIELDS = "id,mimeType,trashed,appProperties,permissions(type)"


class OAuthStateError(ValueError):
    pass


class OAuthConflict(ValueError):
    pass


class DriveOAuth:
    def __init__(self, repository: CaptureRepository, settings: Settings):
        self.repository, self.settings = repository, settings
        with repository.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS drive_oauth_sessions (
                    state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
                    verifier TEXT NOT NULL, root_name TEXT NOT NULL,
                    expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS drive_oauth_connection (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    payload TEXT NOT NULL, status TEXT NOT NULL
                );
            """)

    @contextmanager
    def lock(self):
        descriptor = os.open(
            self.repository.locks / "drive-oauth.lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def cipher(self):
        return Fernet(self.settings.TASK_ENCRYPTION_KEY.encode())

    def configured(self):
        if not all(
            (
                self.settings.DRIVE_CLIENT_ID,
                self.settings.DRIVE_CLIENT_SECRET,
                self.settings.DRIVE_OAUTH_REDIRECT_URI,
                self.settings.TASK_ENCRYPTION_KEY,
            )
        ):
            raise ValueError("Web OAuth configuration is incomplete")
        self.cipher()

    def connection(self):
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT payload,status FROM drive_oauth_connection WHERE id=1"
            ).fetchone()
        if not row:
            return None
        payload = json.loads(self.cipher().decrypt(row["payload"].encode()))
        if payload["client_id"] != self.settings.DRIVE_CLIENT_ID:
            raise OAuthConflict("Restore the OAuth client used by this instance")
        return payload, row["status"]

    def save_connection(self, payload, status):
        encrypted = self.cipher().encrypt(json.dumps(payload).encode()).decode()
        with self.repository.connection() as connection:
            connection.execute(
                "INSERT INTO drive_oauth_connection(id,payload,status) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status=excluded.status",
                (encrypted, status),
            )

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def start(self, root_name):
        self.configured()
        if not root_name.strip() or len(root_name) > 100:
            raise OAuthConflict("Choose a non-empty root name")
        if self.settings.CAPTURE_DESTINATION != "drive":
            raise OAuthConflict("Select Drive as the destination first")
        if self.settings.DRIVE_REFRESH_TOKEN or self.settings.DRIVE_FOLDER_ID:
            raise OAuthConflict("Existing manual Drive configuration requires a separate migration")
        with self.lock():
            existing = self.connection()
            if not existing:
                with self.repository.connection() as connection:
                    managed = connection.execute(
                        "SELECT 1 FROM capture_files f JOIN captures c ON c.id=f.capture_id "
                        "WHERE c.destination='drive' AND f.cloud_id IS NOT NULL LIMIT 1"
                    ).fetchone()
                if managed:
                    raise OAuthConflict("Existing managed cloud files require a separate migration")
            state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            with self.repository.connection() as connection:
                # A new owner request invalidates earlier tabs and bounds retained session data.
                connection.execute("DELETE FROM drive_oauth_sessions")
                connection.execute(
                    "INSERT INTO drive_oauth_sessions(state_hash,browser_hash,verifier,root_name,expires) VALUES(?,?,?,?,?)",
                    (
                        self.digest(state),
                        self.digest(browser),
                        self.cipher().encrypt(verifier.encode()).decode(),
                        root_name,
                        time.time() + SESSION_SECONDS,
                    ),
                )
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.settings.DRIVE_CLIENT_ID,
                "redirect_uri": self.settings.DRIVE_OAUTH_REDIRECT_URI,
                "response_type": "code",
                "scope": DRIVE_SCOPE,
                "access_type": "offline",
                "prompt": "consent select_account",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return url, browser

    def claim(self, state, browser):
        if not state or not browser or len(state) > 100 or len(browser) > 100:
            raise OAuthStateError("Missing browser session")
        with self.repository.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM drive_oauth_sessions WHERE state_hash=?", (self.digest(state),)
            ).fetchone()
            if (
                not row
                or row["used"]
                or row["expires"] < time.time()
                or not secrets.compare_digest(row["browser_hash"], self.digest(browser))
            ):
                raise OAuthStateError("Invalid or expired browser session")
            connection.execute(
                "UPDATE drive_oauth_sessions SET used=1 WHERE state_hash=?", (row["state_hash"],)
            )
        return self.cipher().decrypt(row["verifier"].encode()).decode(), row["root_name"]

    def exchange(self, code, verifier):
        with httpx.Client(
            timeout=self.settings.MODEL_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
        ) as client:
            with client.stream(
                "POST",
                GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.settings.DRIVE_CLIENT_ID,
                    "client_secret": self.settings.DRIVE_CLIENT_SECRET,
                    "redirect_uri": self.settings.DRIVE_OAUTH_REDIRECT_URI,
                    "code_verifier": verifier,
                },
            ) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    content.extend(chunk)
                    if len(content) > 16_384:
                        raise ValueError("Invalid token response")
        token = json.loads(content)
        if (
            not isinstance(token, dict)
            or not token.get("access_token")
            or not token.get("refresh_token")
            or set(token.get("scope", DRIVE_SCOPE).split()) != {DRIVE_SCOPE}
        ):
            raise ValueError("Required Drive grant is missing")
        return token

    def service(self, refresh_token, access_token=None):
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URL,
            client_id=self.settings.DRIVE_CLIENT_ID,
            client_secret=self.settings.DRIVE_CLIENT_SECRET,
            scopes=[DRIVE_SCOPE],
        )
        return build(
            "drive",
            "v3",
            cache_discovery=False,
            http=AuthorizedHttp(
                credentials, http=httplib2.Http(timeout=self.settings.MODEL_TIMEOUT_SECONDS)
            ),
        )

    def callback(self, state, browser, code, *, denied=False):
        self.configured()
        verifier, root_name = self.claim(state, browser)
        with self.lock():
            if denied or not code or len(code) > 4096:
                raise OAuthStateError("Authorization was not completed")
            existing = self.connection()
            token = self.exchange(code, verifier)
            service = self.service(token["refresh_token"], token["access_token"])
            user = service.about().get(fields="user(permissionId,emailAddress)").execute()["user"]
            if not isinstance(user.get("permissionId"), str) or not user["permissionId"]:
                raise ValueError("Google account identity is unavailable")
            if existing and existing[0]["account_id"] != user["permissionId"]:
                raise OAuthConflict("Reconnect using the same Google account")
            folder_id = (
                existing[0]["folder_id"]
                if existing
                else service.files()
                .generateIds(count=1, space="drive", type="files")
                .execute()["ids"][0]
            )
            marker = existing[0]["root_marker"] if existing else secrets.token_hex(16)
            payload = {
                "client_id": self.settings.DRIVE_CLIENT_ID,
                "refresh_token": token["refresh_token"],
                "account_id": user["permissionId"],
                "email": user.get("emailAddress", ""),
                "folder_id": folder_id,
                "root_marker": marker,
                "root_name": root_name,
            }
            # Save the fixed root ID before create: a lost response can be retried without duplication.
            self.save_connection(payload, "pending")
            return self.finish_locked()

    def finish(self):
        self.configured()
        with self.lock():
            return self.finish_locked()

    def finish_locked(self):
        existing = self.connection()
        if not existing:
            raise OAuthConflict("Complete Google authorization first")
        payload, status = existing
        if status == "active":
            return self.status()
        service = self.service(payload["refresh_token"])
        try:
            folder = (
                service.files().get(fileId=payload["folder_id"], fields=FOLDER_FIELDS).execute()
            )
        except HttpError as exc:
            if exc.resp.status != 404:
                raise
            folder = (
                service.files()
                .create(
                    body={
                        "id": payload["folder_id"],
                        "name": payload["root_name"],
                        "mimeType": FOLDER_MIME,
                        "appProperties": {"voice_notion_oauth_root": payload["root_marker"]},
                    },
                    fields=FOLDER_FIELDS,
                )
                .execute()
            )
        if (
            folder.get("id") != payload["folder_id"]
            or folder.get("mimeType") != FOLDER_MIME
            or folder.get("trashed")
            or folder.get("appProperties", {}).get("voice_notion_oauth_root")
            != payload["root_marker"]
            or any(p.get("type") in ("anyone", "domain") for p in folder.get("permissions", []))
        ):
            raise OAuthConflict("OAuth root must be the same private managed folder")
        self.save_connection(payload, "active")
        return self.status()

    def status(self):
        existing = self.connection()
        if not existing:
            return {"connected": False, "pending": False}
        payload, status = existing
        return {
            "connected": status == "active",
            "pending": status == "pending",
            "email": payload["email"],
            "folder_id": payload["folder_id"],
            "folder_url": "https://drive.google.com/drive/folders/" + payload["folder_id"],
        }

    def worker_settings(self):
        existing = self.connection()
        if not existing or existing[1] != "active":
            raise ValueError("Drive Web authorization is incomplete")
        payload = existing[0]
        return self.settings.model_copy(
            update={
                "DRIVE_FOLDER_ID": payload["folder_id"],
                "DRIVE_REFRESH_TOKEN": payload["refresh_token"],
            }
        )
