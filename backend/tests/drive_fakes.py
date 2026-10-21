"""Drive API 離線替身；保留檔案內容以驗證重試、範圍及清理結果。"""

import copy
import hashlib

import httplib2
from googleapiclient.errors import HttpError


class DriveCall:
    def __init__(self, action):
        self.action = action

    def execute(self, **kwargs):
        return self.action()


class DriveUpload:
    def __init__(self, drive, metadata, media):
        self.drive, self.metadata, self.media = drive, metadata, media
        self.resumable_uri, self.resumable_progress = None, 0

    def next_chunk(self, **kwargs):
        self.drive.upload_offsets.append(self.resumable_progress)
        self.resumable_uri = (
            self.resumable_uri or "https://www.googleapis.com/upload/drive/v3/files?upload_id=test"
        )
        if self.drive.interrupt_upload:
            if not self.resumable_progress:
                self.resumable_progress = max(self.media.size() // 2, 1)
                return object(), None
            self.drive.interrupt_upload = False
            raise ConnectionError("test upload interrupted")
        content = self.media.getbytes(0, self.media.size())
        metadata = dict(
            self.metadata,
            size=str(len(content)),
            md5Checksum=hashlib.md5(content, usedforsecurity=False).hexdigest(),
        )
        self.drive.content[metadata["id"]] = content
        self.drive.entries[metadata["id"]] = metadata
        self.resumable_progress = len(content)
        self.drive.operations.append(("upload", metadata["id"]))
        return None, copy.deepcopy(metadata)


class FakeDrive:
    def __init__(self):
        self.entries = {
            "root": {
                "id": "root",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [],
                "permissions": [],
            }
        }
        self.content, self.operations, self.upload_offsets = {}, [], []
        self.counter, self.interrupt_upload = 0, False

    def files(self):
        return self

    def generateIds(self, **kwargs):
        def allocate():
            self.counter += 1
            return {"ids": ["google-id-" + str(self.counter)]}

        return DriveCall(allocate)

    def get(self, fileId, **kwargs):
        def retrieve():
            if fileId not in self.entries:
                raise HttpError(httplib2.Response({"status": "404"}), b"{}")
            return copy.deepcopy(self.entries[fileId])

        return DriveCall(retrieve)

    def create(self, body, media_body=None, **kwargs):
        metadata = dict(body, permissions=[], trashed=False)
        if media_body is not None:
            return DriveUpload(self, metadata, media_body)

        def create_folder():
            self.entries[metadata["id"]] = metadata
            self.operations.append(("folder", metadata["id"]))
            return copy.deepcopy(metadata)

        return DriveCall(create_folder)

    def update(
        self, fileId, media_body=None, body=None, addParents=None, removeParents=None, **kwargs
    ):
        if media_body is not None:
            return DriveUpload(self, self.entries[fileId], media_body)

        def update():
            entry = self.entries[fileId]
            if body:
                entry.update(body)
            if addParents:
                entry["parents"] = [addParents]
            self.operations.append(("trash" if body and body.get("trashed") else "update", fileId))
            return copy.deepcopy(entry)

        return DriveCall(update)

    def get_media(self, fileId, **kwargs):
        return DriveCall(lambda: self.content[fileId])
