"""Notion 純文字來源保存，分批寫入與不確定寫入的保守恢復。"""

from app.services.capture_repository import CaptureRepository
from app.services.notion_service import NotionService

# 官方 rich_text 2000 字及 children 100 個上限，保留 metadata 空間。
NOTION_TEXT_CHUNK = 1800
NOTION_BLOCK_BATCH = 40


class NotionWriteUncertain(RuntimeError):
    """遠端寫入結果未知，找不到 marker 時不盲目重寫。"""


def text_block(text: str, block_type: str = "paragraph") -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


class NotionCaptureStorage:
    def __init__(self, repository: CaptureRepository, service: NotionService):
        self.repository = repository
        self.service = service

    def children(self, page_id: str) -> list[dict]:
        blocks, cursor = [], None
        while True:
            response = self.service.client.blocks.children.list(
                block_id=page_id, start_cursor=cursor
            )
            blocks.extend(response.get("results", []))
            if not response.get("has_more"):
                return blocks
            cursor = response["next_cursor"]

    def save(self, capture_id: str, routing: dict, source_text: str, summary: str) -> str:
        repository = self.repository
        record = repository.get(capture_id)
        if record.kind not in ("text", "audio"):
            raise ValueError("Notion attachments are not supported")
        page = repository.checkpoint(capture_id, "notion_page")
        if page is None:
            if routing["action"] == "append":
                page = self.service.client.pages.retrieve(page_id=routing["target_id"])
            else:
                title = (
                    (routing.get("new_topic_name") or routing["title"])[:150] + " · " + capture_id
                )
                existing = [
                    b
                    for b in self.children(routing["target_id"])
                    if b.get("type") == "child_page" and b["child_page"]["title"] == title
                ]
                if existing:
                    page = self.service.client.pages.retrieve(page_id=existing[0]["id"])
                else:
                    if repository.checkpoint(capture_id, "notion_create_pending"):
                        raise NotionWriteUncertain(
                            "Verify the previous Notion create before retrying"
                        )
                    repository.save_checkpoint(capture_id, "notion_create_pending", True)
                    page = self.service.client.pages.create(
                        parent={"page_id": routing["target_id"]},
                        properties={"title": {"title": [{"text": {"content": title}}]}},
                    )
            repository.save_checkpoint(
                capture_id, "notion_page", {"id": page["id"], "url": page["url"]}
            )
        blocks = [
            text_block(routing["title"][:180], "heading_2"),
            text_block(
                f"來源 ID：{capture_id}；管道：{record.channel}；時間：{record.created_at}；作者未確認"
            ),
            text_block("原始名稱：" + record.original_name),
            text_block("完整原文／未潤飾逐字稿", "heading_3"),
        ]
        blocks.extend(
            text_block(source_text[i : i + NOTION_TEXT_CHUNK])
            for i in range(0, len(source_text), NOTION_TEXT_CHUNK)
        )
        blocks.append(text_block("摘要", "heading_3"))
        blocks.extend(
            text_block(summary[i : i + NOTION_TEXT_CHUNK])
            for i in range(0, len(summary), NOTION_TEXT_CHUNK)
        )
        present = set()
        for block in self.children(page["id"]):
            rich = block.get("paragraph", {}).get("rich_text", [])
            present.add(
                "".join(
                    part.get("plain_text", part.get("text", {}).get("content", "")) for part in rich
                )
            )
        for part, offset in enumerate(range(0, len(blocks), NOTION_BLOCK_BATCH)):
            marker = f"[voice-notion:{capture_id}:part:{part}]"
            if marker in present:
                continue
            pending = repository.checkpoint(capture_id, "notion_pending_part")
            if pending is not None and pending == part:
                raise NotionWriteUncertain("Verify the previous Notion append before retrying")
            repository.save_checkpoint(capture_id, "notion_pending_part", part)
            self.service.client.blocks.children.append(
                block_id=page["id"],
                children=[text_block(marker), *blocks[offset : offset + NOTION_BLOCK_BATCH]],
            )
            repository.save_checkpoint(capture_id, "notion_pending_part", None)
        return page["url"]
