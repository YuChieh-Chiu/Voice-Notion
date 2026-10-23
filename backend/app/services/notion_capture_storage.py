"""Notion 純文字來源保存，分批寫入與不確定寫入的保守恢復。"""

import hashlib
import json

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


def block_fingerprint(block: dict) -> str:
    kind = block.get("type", "")
    text = "".join(
        p.get("plain_text", p.get("text", {}).get("content", ""))
        for p in block.get(kind, {}).get("rich_text", [])
    )
    return hashlib.sha256(json.dumps([kind, text], ensure_ascii=False).encode()).hexdigest()


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
                capture_id,
                "notion_page",
                {
                    "id": page["id"],
                    "url": page["url"],
                    "created_for_capture": routing["action"] == "create",
                    "original_title": title if routing["action"] == "create" else None,
                    "parent_id": routing["target_id"],
                },
            )
        blocks = [
            text_block(routing["title"][:180], "heading_2"),
            text_block(
                f"來源 ID：{capture_id}；管道：{record.channel}；時間：{record.created_at}；作者未確認"
            ),
            text_block("原始名稱：" + record.original_name),
            text_block("完整原文／未潤飾逐字稿", "heading_3"),
        ]
        if record.source_url:
            source = "提供的來源網址：" + record.source_url
            blocks[3:3] = [
                text_block(source[i : i + NOTION_TEXT_CHUNK])
                for i in range(0, len(source), NOTION_TEXT_CHUNK)
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
            if repository.get(capture_id).status in ("delete_pending", "deleted"):
                raise ValueError("Capture removal requested")
            marker = f"[voice-notion:{capture_id}:part:{part}]"
            if marker in present:
                continue
            pending = repository.checkpoint(capture_id, "notion_pending_part")
            if pending is not None and pending == part:
                raise NotionWriteUncertain("Verify the previous Notion append before retrying")
            repository.save_checkpoint(capture_id, "notion_pending_part", part)
            content = [text_block(marker), *blocks[offset : offset + NOTION_BLOCK_BATCH]]
            parts = repository.checkpoint(capture_id, "notion_parts") or {}
            parts[str(part)] = {"marker": marker, "hashes": [block_fingerprint(b) for b in content]}
            repository.save_checkpoint(capture_id, "notion_parts", parts)
            response = self.service.client.blocks.children.append(
                block_id=page["id"],
                children=content,
            )
            if isinstance(response, dict) and len(response.get("results", [])) == len(content):
                parts[str(part)]["ids"] = [b["id"] for b in response["results"]]
                repository.save_checkpoint(capture_id, "notion_parts", parts)
            repository.save_checkpoint(capture_id, "notion_pending_part", None)
        return page["url"]

    def retract(self, capture_id: str):
        page = self.repository.checkpoint(capture_id, "notion_page")
        if not page:
            if self.repository.checkpoint(capture_id, "notion_create_pending"):
                routing = self.repository.checkpoint(capture_id, "routing")
                if not routing or routing.get("action") != "create":
                    raise NotionWriteUncertain("Reconcile pending Notion creation before removal")
                title = (
                    (routing.get("new_topic_name") or routing["title"])[:150] + " · " + capture_id
                )
                candidates = [
                    b
                    for b in self.children(routing["target_id"])
                    if b.get("type") == "child_page" and b["child_page"]["title"] == title
                ]
                if len(candidates) != 1:
                    raise NotionWriteUncertain("Cannot uniquely recover created Notion page")
                remote = self.service.client.pages.retrieve(page_id=candidates[0]["id"])
                page = {
                    "id": remote["id"],
                    "url": remote["url"],
                    "created_for_capture": True,
                    "original_title": title,
                    "parent_id": routing["target_id"],
                }
                self.repository.save_checkpoint(capture_id, "notion_page", page)
            else:
                return
        parts = self.repository.checkpoint(capture_id, "notion_parts") or {}
        blocks = self.children(page["id"])
        available = {b["id"]: b for b in blocks}
        for part in parts.values():
            ids = part.get("ids")
            if not ids:
                matches = [
                    i for i, b in enumerate(blocks) if block_fingerprint(b) == part["hashes"][0]
                ]
                if not matches:
                    raise NotionWriteUncertain("Cannot locate capture blocks for removal")
                candidate = blocks[matches[0] : matches[0] + len(part["hashes"])]
                if [block_fingerprint(b) for b in candidate] != part["hashes"]:
                    raise NotionWriteUncertain("Capture blocks were externally changed")
                ids = part["ids"] = [b["id"] for b in candidate]
                self.repository.save_checkpoint(capture_id, "notion_parts", parts)
            for block_id, expected in zip(ids, part["hashes"]):
                block = available.get(block_id)
                if block is None:
                    remote = self.service.client.blocks.retrieve(block_id=block_id)
                    if isinstance(remote, dict) and (
                        remote.get("archived") is True or remote.get("in_trash") is True
                    ):
                        continue
                    raise NotionWriteUncertain("Known capture block moved or is inaccessible")
                if block_fingerprint(block) != expected:
                    raise NotionWriteUncertain("Capture block was externally changed")
                if block.get("has_children"):
                    raise NotionWriteUncertain("Capture block contains externally added children")
                self.service.client.blocks.delete(block_id=block_id)
        if page.get("created_for_capture"):
            remote_page = self.service.client.pages.retrieve(page_id=page["id"])
            if remote_page.get("archived") is True or remote_page.get("in_trash") is True:
                return
            title_parts = remote_page.get("properties", {}).get("title", {}).get("title", [])
            current_title = "".join(
                p.get("plain_text", p.get("text", {}).get("content", "")) for p in title_parts
            )
            if current_title == page["original_title"]:
                if self.children(page["id"]):
                    self.service.client.pages.update(
                        page_id=page["id"],
                        properties={"title": {"title": [{"text": {"content": capture_id}}]}},
                    )
                else:
                    self.service.client.pages.update(page_id=page["id"], archived=True)
