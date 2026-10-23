"""個人收件查詢及人工校對；與 LINE、HTTP 管理共用。"""

from app.services.capture_repository import CaptureRepository, utc_timestamp


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
        lines = [
            f"{record.id} · {record.status} · {record.category or '未分類'}",
            "全文與摘要：" + ("已人工校對" if record.reviewed_at else "未確認"),
        ]
        if record.destination == "notion":
            lines.append("Notion 純文字，無附件：" + (record.notion_url or "尚未完成"))
        else:
            for file in self.repository.files(capture_id):
                lines.append(
                    f"{file.id} {file.role} {file.size_bytes} bytes · {file.state}"
                    + ("\n" + file.cloud_url if file.cloud_url and file.state == "uploaded" else "")
                )
        return "\n".join(lines)
