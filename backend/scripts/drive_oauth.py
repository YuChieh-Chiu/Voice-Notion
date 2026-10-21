"""手動初始化 Drive OAuth；只在操作者明確執行時開瀏覽器及建立私人根目錄。"""

import argparse
import json
import logging
import os
import re
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def initialize(client_file: Path, output_file: Path, *, folder_id: str | None = None, name="raw"):
    config = json.loads(client_file.read_text())
    installed = config.get("installed", {})
    if not isinstance(installed, dict) or "web" in config:
        raise ValueError("Use only a Google desktop OAuth client")
    if installed.get("token_uri") != "https://oauth2.googleapis.com/token" or installed.get(
        "auth_uri"
    ) not in (
        "https://accounts.google.com/o/oauth2/auth",
        "https://accounts.google.com/o/oauth2/v2/auth",
    ):
        raise ValueError("Use a Google desktop OAuth client with official endpoints")
    if folder_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", folder_id):
        raise ValueError("Invalid folder ID")
    if not name.strip() or len(name) > 100:
        raise ValueError("Invalid folder name")
    fd = os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        logging.getLogger("google_auth_oauthlib").setLevel(logging.CRITICAL)
        flow = InstalledAppFlow.from_client_config(
            {"installed": installed}, [DRIVE_SCOPE], autogenerate_code_verifier=True
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            timeout_seconds=300,
            open_browser=True,
            access_type="offline",
            prompt="consent",
            success_message="授權完成，可以關閉此視窗。",
        )
        if not credentials.refresh_token:
            raise ValueError("Google did not issue a refresh token")
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        if folder_id:
            folder = (
                service.files()
                .get(fileId=folder_id, fields="id,mimeType,trashed,permissions(type)")
                .execute()
            )
        else:
            folder = (
                service.files()
                .create(
                    body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
                    fields="id,mimeType,trashed,permissions(type)",
                )
                .execute()
            )
        if (
            folder.get("mimeType") != "application/vnd.google-apps.folder"
            or folder.get("trashed")
            or any(p.get("type") in ("anyone", "domain") for p in folder.get("permissions", []))
        ):
            raise ValueError("Root must be an accessible private folder")
        values = {
            "DRIVE_CLIENT_ID": credentials.client_id,
            "DRIVE_CLIENT_SECRET": credentials.client_secret,
            "DRIVE_REFRESH_TOKEN": credentials.refresh_token,
            "DRIVE_FOLDER_ID": folder["id"],
        }
        with os.fdopen(fd, "w") as stream:
            fd = None
            stream.write("".join(k + "=" + json.dumps(v) + "\n" for k, v in values.items()))
            stream.flush()
            os.fsync(stream.fileno())
        return folder["id"]
    except BaseException:
        output_file.unlink(missing_ok=True)
        raise
    finally:
        if fd is not None:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client", type=Path, required=True, help="Google desktop client JSON，勿加入 Git"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../.env.drive-oauth"),
        help="新建私人設定檔，不覆寫既有檔案",
    )
    parser.add_argument(
        "--folder-id", help="已授權給此 OAuth app 的既有私人目錄；省略則建立 app 可存取的目錄"
    )
    parser.add_argument("--name", default="raw")
    args = parser.parse_args()
    try:
        folder = initialize(args.client, args.output, folder_id=args.folder_id, name=args.name)
    except Exception as exc:
        parser.exit(
            1, "初始化未完成（" + type(exc).__name__ + "）；請檢查官方 OAuth 授權及目標權限。\n"
        )
    print("設定已寫入私人檔案；根目錄 ID：" + folder)


if __name__ == "__main__":
    main()
