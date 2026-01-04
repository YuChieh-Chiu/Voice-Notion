"""
Notion Service
與 Notion API 互動：搜尋頁面、建立筆記
"""
from typing import List, Dict
from datetime import datetime
from notion_client import Client
from app.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Notion API 限制
NOTION_RICH_TEXT_LIMIT = 2000


class NotionService:
    """Notion API Service"""
    
    def __init__(self):
        self.client = Client(auth=settings.NOTION_TOKEN)
        logger.info("Notion client initialized")
    
    def sync_available_pages(self) -> List[Dict[str, str]]:
        """
        同步取得所有 Integration 可存取的頁面
        
        Returns:
            [{"id": "page_id", "title": "Page Title"}, ...]
        """
        try:
            results = []
            has_more = True
            start_cursor = None
            
            while has_more:
                response = self.client.search(
                    filter={"property": "object", "value": "page"},
                    start_cursor=start_cursor
                )
                
                for page in response.get("results", []):
                    title = ""
                    # 取得標題
                    if "properties" in page:
                        title_prop = page["properties"].get("title") or page["properties"].get("Name")
                        if title_prop and title_prop.get("title"):
                            title = title_prop["title"][0]["plain_text"]
                    
                    results.append({
                        "id": page["id"],
                        "title": title or "Untitled"
                    })
                
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
            
            logger.info(f"Synced {len(results)} Notion pages")
            return results
            
        except Exception as e:
            logger.error(f"Failed to sync Notion pages: {e}", exc_info=True)
            return []
    
    def _split_text(self, text: str, limit: int = NOTION_RICH_TEXT_LIMIT) -> List[Dict]:
        """
        分割長文字為多個 rich_text 物件（處理 Notion 字元限制）
        
        Args:
            text: 原始文字
            limit: 字元限制
            
        Returns:
            [{"text": {"content": "..."}}, ...]
        """
        if len(text) <= limit:
            return [{"text": {"content": text}}]
        
        chunks = []
        for i in range(0, len(text), limit):
            chunks.append({"text": {"content": text[i:i+limit]}})
        return chunks
    
    def create_page(self, parent_id: str, data: Dict) -> str:
        """
        在指定頁面下建立子頁面
        
        Args:
            parent_id: 父頁面 ID
            data: 筆記資料 (from LLM)
            
        Returns:
            建立的頁面 URL
        """
        try:
            new_page = self.client.pages.create(
                parent={"page_id": parent_id},
                properties={
                    "title": {
                        "title": [{"text": {"content": data["title"]}}]
                    }
                },
                children=[
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": self._split_text(data["summary"])
                        }
                    }
                ]
            )
            
            page_url = new_page["url"]
            logger.info(f"Created Notion page: {page_url}")
            
            return page_url
            
        except Exception as e:
            logger.error(f"Failed to create Notion page: {e}", exc_info=True)
            raise
    
    def append_to_page(self, page_id: str, data: Dict) -> str:
        """
        在現有頁面末尾追加內容
        
        Args:
            page_id: 目標頁面 ID
            data: 筆記資料 (包含 title 與 summary)
            
        Returns:
            頁面 URL
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            self.client.blocks.children.append(
                block_id=page_id,
                children=[
                    {"object": "block", "type": "divider", "divider": {}},
                    {
                        "object": "block",
                        "type": "heading_3",
                        "heading_3": {
                            "rich_text": [{"text": {"content": f"📝 {data['title']} ({timestamp})"}}]
                        }
                    },
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": self._split_text(data["summary"])
                        }
                    }
                ]
            )
            
            # 取得頁面 URL
            page = self.client.pages.retrieve(page_id)
            page_url = page["url"]
            logger.info(f"Appended to Notion page: {page_url}")
            
            return page_url
            
        except Exception as e:
            logger.error(f"Failed to append to Notion page: {e}", exc_info=True)
            raise
