"""個人收件查詢及人工校對；與 LINE、HTTP 管理共用。"""

from app.services.capture_repository import CaptureRepository, utc_timestamp
from app.services.line_messages import capture_details


class CaptureManagement:
    def __init__(self, repository: CaptureRepository):
        self.repository = repository

    def review(self, capture_id: str, owner_id: str = "owner"):
        with self.repository.capture_lock(capture_id):
            record = self.repository.get(capture_id, owner_id)
            if record.status != "complete" or record.kind not in ("text", "audio"):
                raise ValueError("只有完整原文與摘要處理完成的收件可以標記校對")
            if any(
                f.state in ("trash_pending", "trashed", "missing")
                for f in self.repository.files(capture_id)
            ):
                raise ValueError("內容已移除，無法標記完整校對")
            self.repository.update_capture(capture_id, reviewed_at=utc_timestamp())
        return self.repository.get(capture_id, owner_id)

    def describe(self, capture_id: str, owner_id: str = "owner") -> str:
        record = self.repository.get(capture_id, owner_id)
        return capture_details(record, self.repository.files(capture_id))
