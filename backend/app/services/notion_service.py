"""
Notion Service
與 Notion API 互動：搜尋頁面、建立筆記
"""
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from notion_client import Client
from app.config import get_settings
from app.core.logger import get_logger
from app.utils.markdown_parser import NotionMarkdownParser

settings = get_settings()
logger = get_logger(__name__)

# Simple In-Memory Cache (since we skipped Redis for now)
# Structure: {"data": result, "expires_at": timestamp}
_page_tree_cache = {}

class NotionService:
    """Notion API Service"""
    
    def __init__(self):
        self.client = Client(auth=settings.NOTION_TOKEN)
        self.md_parser = NotionMarkdownParser()
        logger.info("Notion client initialized")
    
    def sync_page_tree(self) -> Dict[str, List[Dict[str, str]]]:
        """
        同步取得 Page Tree (Roots & Subpages)
        快取 30 分鐘
        
        Returns:
            {
                "roots": [{"id": "...", "title": "..."}],
                "subpages": [{"id": "...", "parent_id": "...", "title": "..."}]
            }
        """
        global _page_tree_cache
        now = datetime.now()
        
        # Check Cache
        if _page_tree_cache and now < _page_tree_cache.get("expires_at", now):
            logger.info("Hit Page Tree Cache")
            return _page_tree_cache["data"]
            
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
                    filter={"property": "object", "value": "page"},
                    start_cursor=start_cursor
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
                    roots.append({
                        "id": page["id"],
                        "title": title
                    })
                
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
                
            # Step 2: Fetch Subpages for each Root
            # This can be slow if many roots. 
            for root in roots:
                children = self.client.blocks.children.list(block_id=root["id"])
                for child in children.get("results", []):
                    if child["object"] == "child_page":
                        subpages.append({
                            "id": child["id"],
                            "parent_id": root["id"],
                            "title": child["child_page"]["title"]
                        })

            result = {
                "roots": roots,
                "subpages": subpages
            }
            
            # Update Cache (30 mins)
            _page_tree_cache = {
                "data": result,
                "expires_at": now + timedelta(minutes=30)
            }
            
            logger.info(f"Synced {len(roots)} roots and {len(subpages)} subpages")
            return result
            
        except Exception as e:
            logger.error(f"Failed to sync Notion page tree: {e}", exc_info=True)
            return {"roots": [], "subpages": []}
    
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
                properties={
                    "title": {
                        "title": [{"text": {"content": title}}]
                    }
                },
                children=content_blocks
            )
            
            # Proactively Update Cache (instead of invalidation)
            # This ensures the new page is immediately available for Append actions
            global _page_tree_cache
            if _page_tree_cache and "data" in _page_tree_cache:
                _page_tree_cache["data"]["subpages"].append({
                    "id": new_page["id"],
                    "parent_id": parent_id,
                    "title": title
                })
                logger.info("Proactively updated Page Tree Cache")
            
            page_url = new_page["url"]
            page_id = new_page["id"]
            logger.info(f"Created Notion subpage: {page_url}")
            
            # 回傳 URL 和 ID，避免依賴 blocks.children.list 的慢索引
            return {"url": page_url, "id": page_id}
            
        except Exception as e:
            logger.error(f"Failed to create Notion subpage: {e}", exc_info=True)
            raise

    def append_to_page(self, page_id: str, title: str, summary_md: str) -> str:
        """
        在現有頁面 (Subpage) 末尾追加內容
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            # 1. Header Block
            header_block = {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"text": {"content": f"📝 {title} ({timestamp})"}}]
                }
            }
            
            # 2. Content Blocks (Parsed from Markdown)
            content_blocks = self.md_parser.parse(summary_md)
            
            # Combine: Divider + Header + Content
            blocks_to_append = [
                {"object": "block", "type": "divider", "divider": {}},
                header_block,
                *content_blocks
            ]
            
            self.client.blocks.children.append(
                block_id=page_id,
                children=blocks_to_append
            )
            
            # 取得頁面 URL (for return)
            page = self.client.pages.retrieve(page_id)
            page_url = page["url"]
            logger.info(f"Appended to Notion page: {page_url}")
            
            return page_url
            
        except Exception as e:
            logger.error(f"Failed to append to Notion page: {e}", exc_info=True)
            raise
