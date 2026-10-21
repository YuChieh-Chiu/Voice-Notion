"""官方 OAuth／Drive SDK：固定 ID、可恢復上傳與受限管理範圍。"""

import fcntl
import hashlib
import io
import os
import uuid
from contextlib import contextmanager
from urllib.parse import urlparse

import httplib2
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from app.config import Settings
from app.schemas.capture import CaptureFile
from app.services.capture_repository import CaptureRepository

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_METADATA_FIELDS = (
    "id,name,mimeType,parents,trashed,appProperties,size,md5Checksum,version,permissions(type)"
)
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024  # Drive 分塊須為 256 KiB 的倍數。


class DriveStorage:
    def __init__(self, repository: CaptureRepository, settings: Settings, service=None):
        self.repository, self.settings = repository, settings
        if not settings.DRIVE_FOLDER_ID:
            raise ValueError("DRIVE_FOLDER_ID is required")
        if service is None:
            if not all(
                (
                    settings.DRIVE_CLIENT_ID,
                    settings.DRIVE_CLIENT_SECRET,
                    settings.DRIVE_REFRESH_TOKEN,
                )
            ):
                raise ValueError("Drive user OAuth credentials are required")
            credentials = Credentials(
                token=None,
                refresh_token=settings.DRIVE_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.DRIVE_CLIENT_ID,
                client_secret=settings.DRIVE_CLIENT_SECRET,
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )
            service = build(
                "drive",
                "v3",
                cache_discovery=False,
                http=AuthorizedHttp(
                    credentials, http=httplib2.Http(timeout=settings.MODEL_TIMEOUT_SECONDS)
                ),
            )
        self.service = service
        with self.folder_lock():
            self.instance_id = repository.system_value("drive_instance")
            if not self.instance_id:
                self.instance_id = uuid.uuid4().hex
                repository.save_system_value("drive_instance", self.instance_id)

    @contextmanager
    def folder_lock(self):
        descriptor = os.open(
            self.repository.locks / "drive-folders.lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def metadata(self, cloud_id: str) -> dict | None:
        try:
            return (
                self.service.files()
                .get(fileId=cloud_id, fields=DRIVE_METADATA_FIELDS)
                .execute(num_retries=2)
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise

    def allocate_id(self) -> str:
        return (
            self.service.files()
            .generateIds(count=1, space="drive", type="files")
            .execute()["ids"][0]
        )

    def assert_private(self, metadata: dict) -> None:
        if metadata.get("trashed") or any(
            p.get("type") in ("anyone", "domain") for p in metadata.get("permissions", [])
        ):
            raise ValueError("Drive target must be active and privately shared")

    def _folder(self, parent: str, name: str) -> str:
        key = "drive_folder:" + hashlib.sha256((parent + ":" + name).encode()).hexdigest()
        with self.folder_lock():
            cloud_id = self.repository.system_value(key)
            if not cloud_id:
                cloud_id = self.allocate_id()
                self.repository.save_system_value(key, cloud_id)
            expected = {"voice_notion_instance": self.instance_id, "voice_notion_folder": key}
            metadata = self.metadata(cloud_id)
            if metadata is None:
                metadata = (
                    self.service.files()
                    .create(
                        body={
                            "id": cloud_id,
                            "name": name,
                            "mimeType": DRIVE_FOLDER_MIME,
                            "parents": [parent],
                            "appProperties": expected,
                        },
                        fields=DRIVE_METADATA_FIELDS,
                    )
                    .execute()
                )
            self.assert_private(metadata)
            if (
                metadata.get("mimeType") != DRIVE_FOLDER_MIME
                or metadata.get("appProperties") != expected
                or parent not in metadata.get("parents", [])
            ):
                raise ValueError("Managed Drive folder metadata mismatch")
            return cloud_id

    def _category_parent(self, category: str | None) -> str:
        root = self.settings.DRIVE_FOLDER_ID
        if category:
            definition = next(
                (c for c in self.settings.CAPTURE_CATEGORIES if c.id == category), None
            )
            if definition and definition.folder_id:
                root = definition.folder_id
        metadata = self.metadata(root)
        if metadata is None or metadata.get("mimeType") != DRIVE_FOLDER_MIME:
            raise ValueError("Configured Drive folder is not accessible")
        self.assert_private(metadata)
        if category and not (definition and definition.folder_id):
            if category not in {c.id for c in self.settings.CAPTURE_CATEGORIES} | {"unclassified"}:
                raise ValueError("Unknown category")
            root = self._folder(root, category)
        return root

    def ensure_capture_folder(self, capture_id: str) -> str:
        record = self.repository.get(capture_id)
        parent = self._category_parent(record.category)
        month = self._folder(parent, record.created_at[:7])
        if record.folder_id:
            metadata = self.metadata(record.folder_id)
            if (
                metadata is None
                or metadata.get("appProperties", {}).get("voice_notion_instance")
                != self.instance_id
            ):
                raise ValueError("Capture folder is missing or belongs to another deployment")
            self.assert_private(metadata)
            self.assert_within_roots(record.folder_id)
            if month not in metadata.get("parents", []):
                self.service.files().update(
                    fileId=record.folder_id,
                    addParents=month,
                    removeParents=",".join(metadata.get("parents", [])),
                    fields="id",
                ).execute()
            return record.folder_id
        folder = self._folder(month, capture_id)
        self.repository.update_capture(capture_id, folder_id=folder)
        return folder

    def expected_properties(self, file: CaptureFile) -> dict:
        return {
            "voice_notion_instance": self.instance_id,
            "voice_notion_capture": file.capture_id,
            "voice_notion_role": file.role,
        }

    def verify_file(self, file: CaptureFile, metadata: dict) -> None:
        record = self.repository.get(file.capture_id)
        if metadata.get("appProperties") != self.expected_properties(
            file
        ) or record.folder_id not in metadata.get("parents", []):
            raise ValueError("Drive file is outside the managed capture")
        self.assert_within_roots(record.folder_id)

    def assert_within_roots(self, folder_id: str) -> None:
        # 整個資料夾若被搬出允許範圍，也不清理或自動搬回。
        allowed = {self.settings.DRIVE_FOLDER_ID} | {
            c.folder_id for c in self.settings.CAPTURE_CATEGORIES if c.folder_id
        }
        ancestor = folder_id
        for _ in range(12):
            if ancestor in allowed:
                root = self.metadata(ancestor)
                if root is None:
                    raise ValueError("Drive root is missing")
                self.assert_private(root)
                return
            folder = self.metadata(ancestor)
            if not folder or folder.get("trashed"):
                break
            self.assert_private(folder)
            parents = folder.get("parents", [])
            if len(parents) != 1:
                break
            ancestor = parents[0]
        raise ValueError("Managed file is outside the configured Drive roots")

    def upload(self, file: CaptureFile, *, replace: bool = False) -> CaptureFile:
        if file.state in ("trash_pending", "trashed", "missing"):
            raise ValueError("Removed files cannot be recreated")
        folder = self.ensure_capture_folder(file.capture_id)
        if not file.cloud_id:
            self.repository.update_file(file.id, cloud_id=self.allocate_id(), state="allocated")
            file = self.repository.file(file.id)
        existing = self.metadata(file.cloud_id)
        content = self.repository.read_file(file)
        if hashlib.sha256(content).hexdigest() != file.sha256:
            raise ValueError("Local source checksum mismatch")
        checksum = hashlib.md5(content, usedforsecurity=False).hexdigest()
        if existing:
            self.verify_file(file, existing)
            self.assert_private(existing)
            if existing.get("md5Checksum") == checksum and int(existing.get("size", -1)) == len(
                content
            ):
                return self._uploaded(file)
            if not replace:
                raise ValueError("Existing Drive content differs; refusing to overwrite")
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype=file.mime_type,
            chunksize=UPLOAD_CHUNK_BYTES,
            resumable=True,
        )
        request = (
            self.service.files().update(
                fileId=file.cloud_id, media_body=media, fields=DRIVE_METADATA_FIELDS
            )
            if existing
            else self.service.files().create(
                body={
                    "id": file.cloud_id,
                    "name": file.name,
                    "parents": [folder],
                    "appProperties": self.expected_properties(file),
                },
                media_body=media,
                fields=DRIVE_METADATA_FIELDS,
            )
        )
        checkpoint = self.repository.checkpoint(file.capture_id, "drive_upload:" + file.id)
        if checkpoint and checkpoint["sha256"] == file.sha256:
            parsed = urlparse(checkpoint["uri"])
            if (
                parsed.scheme != "https"
                or parsed.hostname != "www.googleapis.com"
                or not parsed.path.startswith("/upload/drive/")
            ):
                raise ValueError("Invalid Drive resumable session")
            request.resumable_uri, request.resumable_progress = (
                checkpoint["uri"],
                checkpoint["offset"],
            )
            # 新程序的本機進度可能落後於 Google；SDK 會先以零長度 PUT 查詢 Range。
            request._in_error_state = True
        response = None
        while response is None:
            try:
                _, response = request.next_chunk(num_retries=2)
            except HttpError as exc:
                if exc.resp.status in (404, 410):
                    request.resumable_uri = None
                    self.repository.save_checkpoint(
                        file.capture_id, "drive_upload:" + file.id, None
                    )
                raise
            finally:
                # next_chunk 建立 session 後就開始傳首塊；逾時也必須保留 session。
                if request.resumable_uri:
                    self.repository.save_checkpoint(
                        file.capture_id,
                        "drive_upload:" + file.id,
                        {
                            "uri": request.resumable_uri,
                            "offset": request.resumable_progress,
                            "sha256": file.sha256,
                        },
                    )
        self.verify_file(file, response)
        if response.get("md5Checksum") != checksum or int(response.get("size", -1)) != len(content):
            raise ValueError("Uploaded Drive checksum mismatch")
        self.repository.save_checkpoint(file.capture_id, "drive_upload:" + file.id, None)
        return self._uploaded(file)

    def _uploaded(self, file: CaptureFile) -> CaptureFile:
        self.repository.update_file(
            file.id,
            state="uploaded",
            cloud_url=f"https://drive.google.com/file/d/{file.cloud_id}/view",
        )
        return self.repository.file(file.id)

    def read_cloud_file(self, file: CaptureFile) -> bytes:
        metadata = self.metadata(file.cloud_id)
        if metadata is None or metadata.get("trashed"):
            raise ValueError("Drive file is missing or trashed")
        self.verify_file(file, metadata)
        self.assert_private(metadata)
        if int(metadata.get("size", 0)) > self.settings.CAPTURE_MAX_BYTES:
            raise ValueError("Remote file exceeds the capture limit")
        return self.service.files().get_media(fileId=file.cloud_id).execute(num_retries=2)
