"""
Notion Service
與 Notion API 互動：搜尋頁面、建立筆記
"""

import hashlib
from datetime import datetime
from typing import Dict, List, Optional

from cachetools import TTLCache
from notion_client import Client

from app.config import get_settings
from app.core.logger import get_logger
from app.schemas.context import AuthType, UserContext
from app.utils.markdown_parser import NotionMarkdownParser

settings = get_settings()
logger = get_logger(__name__)

# 使用 TTLCache 防止記憶體洩漏 (1000 個 Token, TTL 30 分鐘)
# Key 為雜湊後的 Token，Value 為 {"data": result, "expires_at": ...}
_token_caches = TTLCache(maxsize=1000, ttl=1800)


class NotionService:
    """Notion API Service"""

    def __init__(self, context: Optional[UserContext] = None):
        if context and context.type == AuthType.DEMO:
            self.is_demo = True
            if not context.notion_token:
                raise ValueError("Demo mode requires a notion_token")
            self.auth_token = context.notion_token
        else:
            self.auth_token = settings.NOTION_TOKEN
            self.is_demo = False

        self.client = Client(auth=self.auth_token, timeout_ms=settings.MODEL_TIMEOUT_SECONDS * 1000)
        self.md_parser = NotionMarkdownParser()

        # 雜湊 Token 用於快取金鑰索引
        self._token_hash = hashlib.sha256(self.auth_token.encode()).hexdigest()

        logger.info(f"Notion client initialized (Mode: {'Demo' if self.is_demo else 'Admin'})")

    def sync_page_tree(self) -> Dict[str, List[Dict[str, str]]]:
        """
        同步取得 Page Tree (Roots & Subpages)
        快取 30 分鐘 (Per Token)
        """
        # TTLCache 會自動處理過期，不再需要手動檢查 expires_at
        cache_data = _token_caches.get(self._token_hash)

        if cache_data:
            logger.info("Hit Page Tree Cache")
            return cache_data

        try:
            # Step 1: Search Roots (All accessible pages)
            # Notion Integration access logic: search returns all pages coupled
            # We assume top-level ones or specific white-listed ones are "Roots"
            # However, search() returns flat list.
            # To simplify "White-list" logic as requested by user -> The integration *is* the whitelist.
            # We treat all directly accessible pages from search() as potential ROOTS if they have no parent (or we just treat them all as potential search targets).
            # But the requirement calls for recursive 1-level check.

            roots = []
            subpages = []

            # Fetch all accessible pages (Roots)
            has_more = True
            start_cursor = None

            while has_more:
                response = self.client.search(
                    filter={"property": "object", "value": "page"}, start_cursor=start_cursor
                )

                for page in response.get("results", []):
                    # 過濾條件：只有「真正的頂層頁面」才是 Root
                    # Notion API 的 search 會回傳所有可存取的頁面，包括：
                    # 1. Top-level pages (parent.type = "workspace")
                    # 2. Subpages (parent.type = "page_id")
                    #
                    # 我們只需要第 1 種作為 Root，第 2 種會透過 blocks.children.list 取得
                    parent = page.get("parent", {})
                    parent_type = parent.get("type")

                    # 只接受 workspace 層級的頁面作為 Root
                    # 過濾掉 parent.type = "page_id" 的頁面（這些是 Subpages）
                    if parent_type != "workspace":
                        continue

                    title = self._extract_title(page)
                    roots.append({"id": page["id"], "title": title})

                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")

            # Step 2: Fetch Subpages for each Root
            # This can be slow if many roots.
            for root in roots:
                cursor = None
                while True:
                    children = self.client.blocks.children.list(
                        block_id=root["id"], start_cursor=cursor
                    )
                    for child in children.get("results", []):
                        if child.get("type") == "child_page":
                            subpages.append(
                                {
                                    "id": child["id"],
                                    "parent_id": root["id"],
                                    "title": child["child_page"]["title"],
                                }
                            )
                    if not children.get("has_more"):
                        break
                    cursor = children["next_cursor"]

            result = {"roots": roots, "subpages": subpages}

            # Update Cache (TTLCache will handle expiration)
            _token_caches[self._token_hash] = result

            logger.info("Synced %s roots and %s subpages", len(roots), len(subpages))
            return result

        except Exception as exc:
            logger.error("Notion tree lookup failed (%s)", type(exc).__name__)
            raise RuntimeError("Notion tree lookup failed") from None

    def _extract_title(self, page: Dict) -> str:
        """Helper to extract title safely"""
        title = ""
        if "properties" in page:
            # Title property name varies ("Name", "title", etc.)
            # But 'title' type property is unique per database/page.
            for prop in page["properties"].values():
                if prop["type"] == "title":
                    if prop["title"]:
                        title = prop["title"][0]["plain_text"]
                    break
        return title or "Untitled"

    def create_subpage(self, parent_id: str, title: str, summary_md: str) -> str:
        """
        在指定 Root (parent_id) 下建立新子頁面 (作為 Topic Page)
        並寫入摘要
        """
        try:
            # 1. Parse Markdown Summary to Blocks
            content_blocks = self.md_parser.parse(summary_md)

            # 2. Create Page with Content
            new_page = self.client.pages.create(
                parent={"page_id": parent_id},
                properties={"title": {"title": [{"text": {"content": title}}]}},
                children=content_blocks,
            )

            # Proactively Update Cache (instead of invalidation)
            if self._token_hash in _token_caches:
                _token_caches[self._token_hash]["subpages"].append(
                    {"id": new_page["id"], "parent_id": parent_id, "title": title}
                )
                logger.info("Proactively updated Page Tree Cache")

            page_url = new_page["url"]
            page_id = new_page["id"]
            logger.info(f"Created Notion subpage: {page_url}")

            # 回傳 URL 和 ID，避免依賴 blocks.children.list 的慢索引
            return {"url": page_url, "id": page_id}

        except Exception as exc:
            logger.error("Notion page creation failed (%s)", type(exc).__name__)
            raise RuntimeError("Notion page creation failed") from None

    def append_to_page(self, page_id: str, title: str, summary_md: str) -> str:
        """
        在現有頁面 (Subpage) 末尾追加內容
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

            # 標題與來源分段呈現。
            header_block = {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": f"📝 {title} ({timestamp})"}}]},
            }

            # 2. Content Blocks (Parsed from Markdown)
            content_blocks = self.md_parser.parse(summary_md)

            # Combine: Divider + Header + Content
            blocks_to_append = [
                {"object": "block", "type": "divider", "divider": {}},
                header_block,
                *content_blocks,
            ]

            self.client.blocks.children.append(block_id=page_id, children=blocks_to_append)

            # 取得頁面 URL (for return)
            page = self.client.pages.retrieve(page_id)
            page_url = page["url"]
            logger.info(f"Appended to Notion page: {page_url}")

            return page_url

        except Exception as exc:
            logger.error("Notion append failed (%s)", type(exc).__name__)
            raise RuntimeError("Notion append failed") from None
