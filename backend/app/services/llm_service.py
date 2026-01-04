"""
LLM Service - Gemini
兩階段 LLM：路由判斷 + 依模板生成摘要
"""
import json
import os
from typing import Dict, List, Any
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
    required=["action", "target_id", "new_topic_name", "title", "template_type"],
    properties={
        "action": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="操作類型：create (在 Root 下建立新子頁面) 或 append (在現有 Subpage 追加內容)",
            enum=["create", "append"]
        ),
        "target_id": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="目標頁面 ID (Create 時為 Root ID, Append 時為 Subpage ID)",
        ),
        "new_topic_name": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="新主題名稱 (僅 Create 時需要，Append 時為空字串)",
        ),
        "title": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="筆記段落標題",
        ),
        "template_type": genai.types.Schema(
            type=genai.types.Type.STRING,
            description="摘要模板：meeting / idea / todo / general",
            enum=["meeting", "idea", "todo", "general"]
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
        page_tree: Dict[str, List[Dict[str, str]]]
    ) -> Dict:
        """
        LLM Stage 1: 路由判斷
        
        Args:
            transcript: 語音逐字稿
            page_tree: { "roots": [], "subpages": [] }
            
        Returns:
            {
                "action": "create" | "append",
                "target_id": "...",
                "new_topic_name": "...",
                "title": "...",
                "template_type": "..."
            }
        """
        try:
            # Format Roots and Subpages for Prompt
            roots_str = "\n".join([f"- {p['title']} (ID: {p['id']})" for p in page_tree.get("roots", [])])
            subpages_str = "\n".join([f"- {p['title']} (ID: {p['id']})" for p in page_tree.get("subpages", [])])
            
            prompt = ROUTING_PROMPT.format(
                transcript=transcript,
                roots=roots_str or "(無)",
                subpages=subpages_str or "(無)"
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
            logger.info(f"Routing result: {result}")
            
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
            
            # 載入共用的規範
            markdown_spec_path = self.templates_dir / "_spec.md"
            markdown_spec = ""
            if markdown_spec_path.exists():
                with open(markdown_spec_path, "r", encoding="utf-8") as f:
                    markdown_spec = f.read()
            
            # 組合：原模板 + 規範
            full_template = template + "\n" + markdown_spec
            
            prompt = full_template.format(transcript=transcript)
            
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
