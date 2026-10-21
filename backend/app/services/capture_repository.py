"""個人部署的持久化收件、內容檔案及跨程序鎖。"""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.capture import CaptureChannel, CaptureFile, CaptureKind, CaptureRecord, FileRole

CAPTURE_ID_PATTERN = re.compile(r"^C[0-9a-f]{16}$")
REMOVED_CAPTURE_STATES = ("delete_pending", "deleted")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaptureRepository:
    """SQLite 與原始內容共用私人 volume；連線不跨 thread 共用。"""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve() / "captures"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.locks = self.root / "locks"
        self.locks.mkdir(exist_ok=True, mode=0o700)
        self.database = self.root / "captures.sqlite3"
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS captures (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, source_key TEXT NOT NULL,
                    channel TEXT NOT NULL, kind TEXT NOT NULL, destination TEXT NOT NULL,
                    original_name TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', category TEXT, reviewed_at TEXT,
                    folder_id TEXT, notion_url TEXT, encrypted_context TEXT, error_code TEXT,
                    notified INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(owner_id, source_key)
                );
                CREATE TABLE IF NOT EXISTS capture_files (
                    id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES captures(id),
                    role TEXT NOT NULL, name TEXT NOT NULL, mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    cloud_id TEXT, cloud_url TEXT, state TEXT NOT NULL DEFAULT 'local',
                    revision INTEGER NOT NULL DEFAULT 0, UNIQUE(capture_id, role)
                );
                CREATE TABLE IF NOT EXISTS capture_checkpoints (
                    capture_id TEXT NOT NULL REFERENCES captures(id), name TEXT NOT NULL,
                    value TEXT NOT NULL, PRIMARY KEY(capture_id, name)
                );
                CREATE TABLE IF NOT EXISTS capture_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        self.database.chmod(0o600)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def directory(self, capture_id: str) -> Path:
        if not CAPTURE_ID_PATTERN.fullmatch(capture_id):
            raise ValueError("Invalid capture ID")
        path = self.root / capture_id
        if path.is_symlink():
            raise ValueError("Capture directories cannot be symlinks")
        return path

    @contextmanager
    def capture_lock(self, capture_id: str):
        """worker 與清理串行操作同一來源；鎖檔不隨內容清除。"""
        self.directory(capture_id)
        descriptor = os.open(self.locks / f"{capture_id}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def get(self, capture_id: str, owner_id: str | None = None) -> CaptureRecord:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        if row is None or (owner_id is not None and row["owner_id"] != owner_id):
            raise KeyError("Capture not found")
        return CaptureRecord(**dict(row))

    def list_captures(
        self, owner_id: str, limit: int = 100, offset: int = 0
    ) -> list[CaptureRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM captures WHERE owner_id=? ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                (owner_id, min(max(limit, 1), 500), max(offset, 0)),
            ).fetchall()
        return [CaptureRecord(**dict(row)) for row in rows]

    def create(
        self,
        *,
        owner_id: str,
        channel: CaptureChannel,
        kind: CaptureKind,
        destination: str,
        content: bytes,
        original_name: str,
        mime_type: str,
        source_key: str | None = None,
        encrypted_context: str | None = None,
        stored_extension: str | None = None,
    ) -> tuple[CaptureRecord, bool]:
        """先可靠寫入位元組再登記；重送不覆蓋既有來源或刪除標記。"""
        source_key = source_key or uuid.uuid4().hex
        record_id = "C" + uuid.uuid4().hex[:16]
        name = original_name.replace("\\", "/").split("/")[-1]
        name = re.sub(r"[\x00-\x1f\x7f]", " ", name)[:255] or "attachment"
        extension = stored_extension or Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".bin"
        filename = "original" + (".txt" if kind == "text" else extension)
        digest = hashlib.sha256(content).hexdigest()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM captures WHERE owner_id=? AND source_key=?",
                (owner_id, source_key),
            ).fetchone()
            if existing:
                return CaptureRecord(**dict(existing)), False
            record = CaptureRecord(
                id=record_id,
                owner_id=owner_id,
                source_key=source_key,
                channel=channel,
                kind=kind,
                destination=destination,
                original_name=name,
                created_at=utc_timestamp(),
                encrypted_context=encrypted_context,
            )
            values = record.model_dump()
            values["encrypted_context"] = encrypted_context
            columns = ",".join(values)
            conn.execute(
                f"INSERT INTO captures ({columns}) VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            self._write_file(record_id, filename, content)
            conn.execute(
                "INSERT INTO capture_files(id,capture_id,role,name,mime_type,size_bytes,sha256) "
                "VALUES(?,?,'original',?,?,?,?)",
                ("F" + uuid.uuid4().hex[:16], record_id, filename, mime_type, len(content), digest),
            )
        return record, True

    def _write_file(self, capture_id: str, name: str, content: bytes) -> None:
        directory = self.directory(capture_id)
        directory.mkdir(exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, directory / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def files(self, capture_id: str) -> list[CaptureFile]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM capture_files WHERE capture_id=? ORDER BY role",
                (capture_id,),
            ).fetchall()
        return [CaptureFile(**dict(row)) for row in rows]

    def file(self, file_id: str) -> CaptureFile:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM capture_files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            raise KeyError("File not found")
        return CaptureFile(**dict(row))

    def read_file(self, file: CaptureFile) -> bytes:
        if Path(file.name).name != file.name:
            raise ValueError("Invalid stored filename")
        path = self.directory(file.capture_id) / file.name
        if path.is_symlink():
            raise ValueError("Stored files cannot be symlinks")
        return path.read_bytes()

    def save_artifact(self, capture_id: str, role: FileRole, content: bytes) -> CaptureFile:
        if role not in ("transcript", "note"):
            raise ValueError("Original sources cannot be overwritten")
        name = "transcript.txt" if role == "transcript" else "note.md"
        mime = "text/plain" if role == "transcript" else "text/markdown"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = conn.execute(
                "SELECT status FROM captures WHERE id=?", (capture_id,)
            ).fetchone()
            if record is None or record["status"] in REMOVED_CAPTURE_STATES:
                raise ValueError("Capture is removed")
            existing = conn.execute(
                "SELECT * FROM capture_files WHERE capture_id=? AND role=?",
                (capture_id, role),
            ).fetchone()
            if existing and existing["state"] in ("trash_pending", "trashed", "missing"):
                raise ValueError("Removed artifacts cannot be recreated")
            self._write_file(capture_id, name, content)
            file_id = existing["id"] if existing else "F" + uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT INTO capture_files(id,capture_id,role,name,mime_type,size_bytes,sha256) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(capture_id,role) DO UPDATE SET "
                "size_bytes=excluded.size_bytes,sha256=excluded.sha256,revision=revision+1",
                (
                    file_id,
                    capture_id,
                    role,
                    name,
                    mime,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                ),
            )
        return self.file(file_id)

    def update_capture(self, capture_id: str, **fields) -> None:
        allowed = {
            "status",
            "category",
            "reviewed_at",
            "folder_id",
            "notion_url",
            "error_code",
            "notified",
            "encrypted_context",
        }
        if not fields or fields.keys() - allowed:
            raise ValueError("Invalid capture update")
        with self.connection() as conn:
            conn.execute(
                "UPDATE captures SET "
                + ",".join(f"{key}=?" for key in fields)
                + ",revision=revision+1 WHERE id=?",
                (*fields.values(), capture_id),
            )

    def update_file(self, file_id: str, **fields) -> None:
        if not fields or fields.keys() - {"cloud_id", "cloud_url", "state"}:
            raise ValueError("Invalid file update")
        with self.connection() as conn:
            conn.execute(
                "UPDATE capture_files SET "
                + ",".join(f"{key}=?" for key in fields)
                + ",revision=revision+1 WHERE id=?",
                (*fields.values(), file_id),
            )

    def remove_local_files(self, capture_id: str) -> None:
        for file in self.files(capture_id):
            path = self.directory(capture_id) / file.name
            path.unlink(missing_ok=True)

    def checkpoint(self, capture_id: str, name: str):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM capture_checkpoints WHERE capture_id=? AND name=?",
                (capture_id, name),
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def save_checkpoint(self, capture_id: str, name: str, value) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO capture_checkpoints(capture_id,name,value) VALUES(?,?,?) "
                "ON CONFLICT(capture_id,name) DO UPDATE SET value=excluded.value",
                (capture_id, name, json.dumps(value, ensure_ascii=False)),
            )

    def system_value(self, name: str):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM capture_settings WHERE name=?", (name,)
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def save_system_value(self, name: str, value) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO capture_settings(name,value) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (name, json.dumps(value)),
            )
