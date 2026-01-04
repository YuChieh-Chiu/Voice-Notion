"""
LLM Service - Gemini
兩階段 LLM：路由判斷 + 依模板生成摘要
"""
import json
import os
from typing import Dict, List
from pathlib import Path
from google import genai
from google.genai import types
from app.config import get_settings
from app.core.logger import get_logger
from app.prompts.routing import ROUTING_PROMPT

settings = get_settings()
logger = get_logger(__name__)

# Schema：路由判斷
ROUTING_SCHEMA = genai.types.Schema(
    type=genai.types.Type.OBJECT,
    required=["action", "title", "template_type", "target_page_id"],
    properties={
        "action": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="操作類型：create (建立新頁面) 或 append (追加到現有頁面)",
        ),
        "title": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="筆記標題",
        ),
        "template_type": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="摘要模板：meeting / idea / todo / general",
        ),
        "target_page_id": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="選定的 Notion 頁面 ID",
        ),
    },
)


class LLMService:
    """Large Language Model Service - 兩階段架構"""
    
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.templates_dir = Path(__file__).parent.parent / "prompts" / "templates"
        logger.info("Gemini client initialized")
    
    def route(
        self, 
        transcript: str, 
        available_pages: List[Dict[str, str]]
    ) -> Dict:
        """
        LLM Stage 1: 路由判斷
        
        Args:
            transcript: 語音逐字稿
            available_pages: 可用的 Notion 頁面清單
            
        Returns:
            {
                "action": "create" | "append",
                "title": "筆記標題",
                "template_type": "meeting" | "idea" | "todo" | "general",
                "target_page_id": "xxx"
            }
        """
        try:
            # 建立頁面清單字串
            pages_str = "\n".join([f"- {p['title']} (ID: {p['id']})" for p in available_pages])
            
            prompt = ROUTING_PROMPT.format(
                transcript=transcript,
                pages=pages_str
            )
            
            contents = [types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)]
            )]
            
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ROUTING_SCHEMA,
                temperature=0.0
            )
            
            response = self.client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=contents,
                config=config
            )
            
            result = json.loads(response.text)
            logger.info(f"Routing: action={result['action']}, template={result['template_type']}, page={result['target_page_id']}")
            
            return result
            
        except Exception as e:
            logger.error(f"LLM routing failed: {e}", exc_info=True)
            raise
    
    def summarize(self, transcript: str, template_type: str) -> str:
        """
        LLM Stage 2: 依模板生成摘要
        
        Args:
            transcript: 語音逐字稿
            template_type: 模板類型
            
        Returns:
            摘要內容（依模板格式）
        """
        try:
            # 載入模板
            template_path = self.templates_dir / f"{template_type}.md"
            if not template_path.exists():
                logger.warning(f"Template {template_type} not found, using general")
                template_path = self.templates_dir / "general.md"
            
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
            
            prompt = template.format(transcript=transcript)
            
            contents = [types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)]
            )]
            
            config = types.GenerateContentConfig(
                temperature=1.0
            )
            
            response = self.client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=contents,
                config=config
            )
            
            summary = response.text
            logger.info(f"Summary generated using template: {template_type}")
            
            return summary
            
        except Exception as e:
            logger.error(f"LLM summarization failed: {e}", exc_info=True)
            raise
