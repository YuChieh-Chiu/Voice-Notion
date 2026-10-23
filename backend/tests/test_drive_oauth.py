import json
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import drive_oauth


def test_oauth_setup_writes_private_settings_and_never_overwrites(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        )
    )
    flow = MagicMock()
    flow.run_local_server.return_value = SimpleNamespace(
        client_id="test-client", client_secret="test-client-value", refresh_token="test-refresh"
    )
    monkeypatch.setattr(
        drive_oauth.InstalledAppFlow, "from_client_config", MagicMock(return_value=flow)
    )
    api = MagicMock()
    api.files.return_value.create.return_value.execute.return_value = {
        "id": "test-folder",
        "mimeType": "application/vnd.google-apps.folder",
        "permissions": [],
    }
    monkeypatch.setattr(drive_oauth, "build", MagicMock(return_value=api))
    output = tmp_path / ".env.drive-oauth"
    assert drive_oauth.initialize(client, output) == "test-folder"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert 'DRIVE_REFRESH_TOKEN="test-refresh"' in output.read_text()
    assert flow.run_local_server.call_args.kwargs["host"] == "127.0.0.1"
    with pytest.raises(FileExistsError):
        drive_oauth.initialize(client, output)
    assert output.exists() and api.files.return_value.create.call_count == 1


def test_oauth_setup_rejects_non_google_endpoints_before_browser(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "auth_uri": "https://evil.example/auth",
                    "token_uri": "https://evil.example/token",
                }
            }
        )
    )
    browser = MagicMock()
    monkeypatch.setattr(drive_oauth.InstalledAppFlow, "from_client_config", browser)
    with pytest.raises(ValueError):
        drive_oauth.initialize(client, tmp_path / "output")
    browser.assert_not_called()


def test_oauth_setup_rejects_mixed_client_types_before_sdk_selection(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                },
                "web": {
                    "auth_uri": "https://example.invalid/auth",
                    "token_uri": "https://example.invalid/token",
                },
            }
        )
    )
    browser = MagicMock()
    monkeypatch.setattr(drive_oauth.InstalledAppFlow, "from_client_config", browser)
    with pytest.raises(ValueError, match="desktop"):
        drive_oauth.initialize(client, tmp_path / "output")
    browser.assert_not_called()
