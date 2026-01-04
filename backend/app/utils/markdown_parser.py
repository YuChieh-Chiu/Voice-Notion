"""
Markdown to Notion Blocks Parser
使用 markdown-it-py 將 Markdown 轉換為 Notion API 相容的 Block 物件
"""
from typing import List, Dict, Any
from markdown_it import MarkdownIt
from markdown_it.token import Token

# Notion API 限制
NOTION_RICH_TEXT_LIMIT = 2000

class NotionMarkdownParser:
    def __init__(self):
        self.md = MarkdownIt("commonmark", {"breaks": True, "html": False})
        
    def parse(self, text: str) -> List[Dict[str, Any]]:
        """
        將 Markdown 文字轉換為 Notion Block List
        """
        tokens = self.md.parse(text)
        blocks = []
        
        i = 0
        while i < len(tokens):
            token = tokens[i]
            
            if token.type == "heading_open":
                # 處理標題 (H1-H3)
                level = int(token.tag[1])
                # Notion 只有 h1, h2, h3
                notion_level = min(max(level, 1), 3) 
                
                # 獲取 inline內容
                inline_token = tokens[i + 1]
                content_blocks = self._parse_inline(inline_token)
                
                blocks.append({
                    "object": "block",
                    "type": f"heading_{notion_level}",
                    f"heading_{notion_level}": {
                        "rich_text": content_blocks
                    }
                })
                i += 3 # skip inline & close
                
            elif token.type == "bullet_list_open":
                # 處理無序列表
                # 遍歷直到 bullet_list_close
                i += 1
                while i < len(tokens) and tokens[i].type != "bullet_list_close":
                     if tokens[i].type == "list_item_open":
                         # list item 內容通常包在 paragraph 中
                         # list_item_open -> paragraph_open -> inline -> paragraph_close -> list_item_close
                         # 簡化處理：假設結構固定
                         if i + 2 < len(tokens) and tokens[i+1].type == "paragraph_open":
                             inline_token = tokens[i+2]
                             content_blocks = self._parse_inline(inline_token)
                             
                             blocks.append({
                                 "object": "block",
                                 "type": "bulleted_list_item",
                                 "bulleted_list_item": {
                                     "rich_text": content_blocks
                                 }
                             })
                             i += 4 # skip para & close
                         else:
                             i += 1
                     else:
                         i += 1
                i += 1 # skip list close

            elif token.type == "ordered_list_open":
                # 處理有序列表
                i += 1
                while i < len(tokens) and tokens[i].type != "ordered_list_close":
                     if tokens[i].type == "list_item_open":
                         if i + 2 < len(tokens) and tokens[i+1].type == "paragraph_open":
                             inline_token = tokens[i+2]
                             content_blocks = self._parse_inline(inline_token)
                             
                             blocks.append({
                                 "object": "block",
                                 "type": "numbered_list_item",
                                 "numbered_list_item": {
                                     "rich_text": content_blocks
                                 }
                             })
                             i += 4
                         else:
                             i += 1
                     else:
                         i += 1
                i += 1
                
            elif token.type == "paragraph_open":
                # 一般段落
                # paragraph_open -> inline -> paragraph_close
                if i + 1 < len(tokens) and tokens[i+1].type == "inline":
                    inline_token = tokens[i+1]
                    content_blocks = self._parse_inline(inline_token)
                    
                    if content_blocks: # 忽略空行
                        blocks.append({
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": content_blocks
                            }
                        })
                    i += 3
                else:
                    i += 1
            
                i += 1
            
            elif token.type == "fence" or token.type == "code_block":
                # 程式碼區塊 (Fenced or Indented)
                if token.type == "fence":
                    # Fenced code: extract language from info string
                    lang = token.info.strip() if token.info else ""
                    # Take first word if multi-word (e.g., "python copy" -> "python")
                    # But preserve known multi-word languages like "plain text"
                    if lang and lang != "plain text":
                        lang = lang.split()[0]
                    elif not lang:
                        lang = "plain text"
                else:
                    # Indented code block: always plain text
                    lang = "plain text"
                    
                content = token.content.strip()
                
                blocks.append({
                    "object": "block",
                    "type": "code",
                    "code": {
                        "language": lang,
                        "rich_text": self._create_text_chunks(content)
                    }
                })
                i += 1

            elif token.type == "blockquote_open":
                # 引用區塊
                # blockquote_open -> paragraph_open -> inline -> paragraph_close -> blockquote_close
                # 簡化處理：取第一個 paragraph 的內容
                i += 1
                while i < len(tokens) and tokens[i].type != "blockquote_close":
                    if tokens[i].type == "paragraph_open":
                        if i + 1 < len(tokens) and tokens[i+1].type == "inline":
                            inline_token = tokens[i+1]
                            content_blocks = self._parse_inline(inline_token)
                            
                            blocks.append({
                                "object": "block",
                                "type": "quote",
                                "quote": {
                                    "rich_text": content_blocks
                                }
                            })
                            i += 3 # skip inline & para_close
                        else:
                            i += 1
                    else:
                        i += 1
                i += 1 # skip blockquote_close
                
            elif token.type == "hr":
                # 分隔線
                blocks.append({
                    "object": "block",
                    "type": "divider",
                    "divider": {}
                })
                i += 1

            else:
                # 其他未處理類型跳過
                i += 1
                
        return blocks

    def _parse_inline(self, token: Token) -> List[Dict[str, Any]]:
        """
        解析 inline token children (Bold, Italic, Link, Text)
        
        與完整 Markdown Parser 的差異說明 (Simplified vs Full):
        1. 巢狀結構簡化 (Flatten): 
           不遞迴處理巢狀 Block (如 Blockquote 內有 List)，僅取第一層內容。
        2. 連結處理簡化:
           Markdown-it 的 Link 是 open -> text -> close 結構。
           此處使用簡單狀態變數 (current_url) 捕捉，若有巢狀連結可能會有邊際情況(但 Markdown spec 通常不允許 nested links)。
        3. 自定義分割:
           因為 Notion 限制 rich_text 內容最多 2000 字元，
           此處會在生成 text object 時主動切分 (Chunking)。
        """
        if not token.children:
            return []
            
        result = []
        current_style = {
            "bold": False,
            "italic": False,
            "strikethrough": False,
            "code": False,
        }
        current_url = None # 用於追蹤當前連結 URL
        
        for child in token.children:
            if child.type == "text":
                content = child.content
                # Fix: 若單一文字 Token 超過 2000 字，需切分
                chunks = self._create_text_chunks(content)
                
                for chunk in chunks:
                    text_obj = {
                        "type": "text",
                        "text": {
                            "content": chunk["text"]["content"]
                        },
                        "annotations": current_style.copy()
                    }
                    # Add Link if active
                    if current_url:
                        text_obj["text"]["link"] = {"url": current_url}
                        
                    result.append(text_obj)
                    
            elif child.type == "code_inline":
                # Inline code also needs chunking check (though unlikely to be huge)
                content = child.content
                chunks = self._create_text_chunks(content)
                for chunk in chunks:
                    result.append({
                       "type": "text",
                       "text": {
                           "content": chunk["text"]["content"]
                       },
                       "annotations": {**current_style, "code": True}
                   })
                   
            elif child.type == "strong_open":
                current_style["bold"] = True
            elif child.type == "strong_close":
                current_style["bold"] = False
            elif child.type == "em_open":
                current_style["italic"] = True
            elif child.type == "em_close":
                current_style["italic"] = False
            elif child.type == "link_open":
                # Get URL from attrs (list of [key, value])
                # attrs stored as dict in python wrapper often, or use .attrGet
                current_url = child.attrGet("href")
            elif child.type == "link_close":
                current_url = None
            
        # Merge adjacent text blocks with same style AND same link to reduce block count
        merged_result = []
        if not result:
            return []
            
        current = result[0]
        for next_item in result[1:]:
            # Check if annotations AND link are same
            same_style = current["annotations"] == next_item["annotations"]
            
            # Check Link equality
            curr_link = current["text"].get("link", {}).get("url")
            next_link = next_item["text"].get("link", {}).get("url")
            same_link = curr_link == next_link
            
            # Check Length Limit
            fits_limit = len(current["text"]["content"]) + len(next_item["text"]["content"]) <= NOTION_RICH_TEXT_LIMIT

            if same_style and same_link and fits_limit:
                current["text"]["content"] += next_item["text"]["content"]
            else:
                merged_result.append(current)
                current = next_item
        merged_result.append(current)
        
        return merged_result

    def _create_text_chunks(self, text: str) -> List[Dict]:
        """分割長文字 (Helper)"""
        if len(text) <= NOTION_RICH_TEXT_LIMIT:
             return [{"text": {"content": text}}]
             
        chunks = []
        for i in range(0, len(text), NOTION_RICH_TEXT_LIMIT):
            chunks.append({"text": {"content": text[i:i+NOTION_RICH_TEXT_LIMIT]}})
        return chunks
