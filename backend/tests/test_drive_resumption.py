"""以真實 SDK 與離線 HTTP 驗證跨程序續傳，而非模擬 next_chunk。"""

import hashlib
import json

import httplib2
import pytest
from drive_fakes import FakeDrive
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

from app.config import Settings
from app.services.capture_repository import CaptureRepository
from app.services.drive_storage import DriveStorage


class UploadTransport:
    def __init__(self, drive, metadata, *, final_lost=False):
        self.drive, self.metadata = drive, metadata
        self.received, self.calls = b"", []
        self.interrupt, self.final_lost, self.expired = True, final_lost, None

    def request(self, uri, method, body=None, headers=None, **kwargs):
        headers = {k.lower(): v for k, v in headers.items()}
        self.calls.append((method, headers.get("content-range")))
        if method == "POST":
            return httplib2.Response(
                {
                    "status": "200",
                    "location": "https://www.googleapis.com/upload/drive/v3/files?upload_id=offline",
                }
            ), b""
        if self.expired:
            return httplib2.Response({"status": str(self.expired)}), b"{}"
        if headers["content-range"].startswith("bytes */"):
            if len(self.received) == 4:
                return self.finish()
            return httplib2.Response({"status": "308", "range": "bytes=0-1"}), b""
        content = body.read()
        if self.interrupt:
            self.interrupt = False
            self.received = content if self.final_lost else content[:2]
            if self.final_lost:
                self.finish()
            raise TimeoutError("offline response lost")
        assert headers["content-range"] == "bytes 2-3/4"
        self.received += content
        return self.finish()

    def finish(self):
        metadata = dict(
            self.metadata,
            size="4",
            md5Checksum=hashlib.md5(self.received, usedforsecurity=False).hexdigest(),
        )
        self.drive.entries[metadata["id"]] = metadata
        self.drive.content[metadata["id"]] = self.received
        return httplib2.Response({"status": "200"}), json.dumps(metadata).encode()


@pytest.fixture
def sdk_setup(tmp_path):
    repository = CaptureRepository(tmp_path)
    settings = Settings(_env_file=None, DRIVE_FOLDER_ID="root")
    fake = FakeDrive()
    storage = DriveStorage(repository, settings, service=fake)
    record, _ = repository.create(
        owner_id="owner",
        channel="http",
        kind="text",
        destination="drive",
        content=b"abcd",
        original_name="text.txt",
        mime_type="text/plain",
    )
    folder = storage.ensure_capture_folder(record.id)
    file = repository.files(record.id)[0]
    repository.update_file(file.id, cloud_id="fixed-file")
    file = repository.file(file.id)
    transport = UploadTransport(
        fake,
        {
            "id": file.cloud_id,
            "parents": [folder],
            "appProperties": storage.expected_properties(file),
        },
    )

    def create(body, media_body=None, **kwargs):
        return HttpRequest(
            transport,
            lambda resp, content: json.loads(content),
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
            method="POST",
            body=json.dumps(body),
            resumable=media_body,
        )

    fake.create = create
    return repository, storage, file, transport


def test_first_chunk_loss_persists_session_and_probes_server_offset(sdk_setup):
    repository, storage, file, transport = sdk_setup
    with pytest.raises(TimeoutError):
        storage.upload(file)
    checkpoint = repository.checkpoint(file.capture_id, "drive_upload:" + file.id)
    assert checkpoint["uri"] and checkpoint["offset"] == 0
    storage.upload(repository.file(file.id))
    assert transport.calls[2:] == [("PUT", "bytes */4"), ("PUT", "bytes 2-3/4")]
    assert transport.received == b"abcd"


@pytest.mark.parametrize("status", [404, 410])
def test_expired_session_is_cleared(sdk_setup, status):
    repository, storage, file, transport = sdk_setup
    with pytest.raises(TimeoutError):
        storage.upload(file)
    transport.expired = status
    with pytest.raises(HttpError):
        storage.upload(repository.file(file.id))
    assert repository.checkpoint(file.capture_id, "drive_upload:" + file.id) is None


def test_final_response_loss_recovers_existing_fixed_id_without_upload(sdk_setup):
    repository, storage, file, transport = sdk_setup
    transport.final_lost = True
    with pytest.raises(TimeoutError):
        storage.upload(file)
    before = list(transport.calls)
    assert storage.upload(repository.file(file.id)).state == "uploaded"
    assert transport.calls == before
