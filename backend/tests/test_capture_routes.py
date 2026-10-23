from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.request_limits import RequestBodyLimitMiddleware
from app.main import app
from app.routes import voice_note
from app.services.capture_repository import CaptureRepository


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_note.settings, "CAPTURE_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        voice_note.process_capture, "delay", MagicMock(return_value=MagicMock(id="task-test"))
    )
    return TestClient(app, base_url="http://localhost"), tmp_path


def test_compatible_siri_response_and_queue_only_contains_id(client):
    http, directory = client
    content = b"\x00\x00\x00\x20ftypM4A " + b"data"
    response = http.post(
        "/api/v1/note/ios", content=content, headers={"X-API-Key": "test-personal"}
    )
    assert response.status_code == 202
    data = response.json()
    assert data["task_id"] == "task-test" and data["capture_id"].startswith("C")
    assert voice_note.process_capture.delay.call_args.args == (data["capture_id"],)
    repository = CaptureRepository(directory)
    assert repository.read_file(repository.files(data["capture_id"])[0]) == content


def test_text_requires_personal_auth_and_never_echoes_invalid_input(client, caplog):
    http, _ = client
    assert http.post("/api/v1/captures/text", json={"text": "private"}).status_code == 401
    response = http.post(
        "/api/v1/captures/text",
        json={"text": {"sensitive": "test-private-input"}},
        headers={"X-API-Key": "test-personal"},
    )
    assert response.status_code == 422
    assert "test-private-input" not in response.text + caplog.text


def test_queue_failure_still_preserves_source(client, monkeypatch):
    http, directory = client
    monkeypatch.setattr(
        voice_note.process_capture, "delay", MagicMock(side_effect=RuntimeError("broker offline"))
    )
    response = http.post(
        "/api/v1/captures/text", json={"text": "完整原文"}, headers={"X-API-Key": "test-personal"}
    )
    assert response.status_code == 503
    repository = CaptureRepository(directory)
    record = repository.get(response.json()["detail"]["capture_id"])
    assert repository.read_file(repository.files(record.id)[0]).decode() == "完整原文"


def test_original_filename_is_preserved_but_audio_extension_uses_magic(client):
    http, directory = client
    content = b"\x00\x00\x00\x20ftypM4A " + b"data"
    response = http.post(
        "/api/v1/note",
        files={"audio": ("../../meeting.jpg", content)},
        headers={"X-API-Key": "test-personal"},
    )
    assert response.status_code == 202
    repository = CaptureRepository(directory)
    record = repository.get(response.json()["capture_id"])
    assert record.original_name == "meeting.jpg"
    assert repository.files(record.id)[0].name == "original.m4a"


def test_chunked_body_is_limited_before_parser():
    limited = FastAPI()
    limited.add_middleware(RequestBodyLimitMiddleware, max_bytes=10)

    @limited.post("/read")
    async def read(request: Request):
        return {"size": len(await request.body())}

    response = TestClient(limited).post("/read", content=iter([b"123456", b"789012"]))
    assert response.status_code == 413
