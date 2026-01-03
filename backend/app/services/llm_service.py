"""
LLM Service - Gemini
使用 Google Gemini API 進行摘要與智慧路由
"""
import json
from typing import Dict, List
from google import genai
from google.genai import types
from app.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class LLMService:
    """Large Language Model Service"""
    
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Gemini client initialized")
    
    def summarize_and_route(
        self, 
        transcript: str, 
        available_pages: List[Dict[str, str]]
    ) -> Dict:
        """
        分析逐字稿，生成摘要並判斷應存入哪個 Notion 頁面
        
        Args:
            transcript: 語音逐字稿
            available_pages: 可用的 Notion 頁面清單 [{"id": "xxx", "title": "Work"}, ...]
            
        Returns:
            {
                "title": "筆記標題",
                "summary": "摘要內容",
                "action_items": ["待辦1", "待辦2"],
                "target_page_id": "xxx"  # 選定的頁面 ID
            }
        """
        try:
            # 建立頁面清單字串
            pages_str = "\n".join([f"- {p['title']} (ID: {p['id']})" for p in available_pages])
            
            prompt = f"""你是一個專業的筆記助理。請分析以下語音逐字稿，並完成以下任務：

1. 生成一個簡潔的標題 (10字以內)
2. 撰寫重點摘要 (50字左右)
3. 提取待辦事項 (若有)
4. 判斷此筆記應該存入哪個 Notion 頁面

逐字稿：
{transcript}

可用的 Notion 頁面：
{pages_str}

請以 JSON 格式回覆，包含以下欄位：
{{
    "title": "筆記標題",
    "summary": "摘要內容",
    "action_items": ["待辦1", "待辦2"] (若無則為空陣列),
    "target_page_id": "選定的頁面 ID" (若無法判斷則使用 "Inbox")
}}"""

            contents = [types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)]
            )]
            
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3
            )
            
            response = self.client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=contents,
                config=config
            )
            
            result = json.loads(response.text)
            logger.info(f"LLM analysis completed: {result['title']}")
            
            return result
            
        except Exception as e:
            logger.error(f"LLM service failed: {e}", exc_info=True)
            raise
