"""
Markdown to Notion Blocks Parser
使用 mistune 將 Markdown 轉換為 Notion API 相容的 Block 物件
"""
from typing import List, Dict, Any, Optional
import mistune
from mistune.plugins.table import table
from mistune.plugins.task_lists import task_lists

# Notion API 限制
# REFERENCE: https://developers.notion.com/reference/request-limits
NOTION_RICH_TEXT_LIMIT = 2000
NOTION_BLOCK_CHILDREN_LIMIT = 100

class NotionMarkdownParser:
    def __init__(self):
        # 初始化 mistune，啟用表格與任務清單插件
        self.md = mistune.create_markdown(renderer=None, plugins=[table, task_lists])
        
    def parse(self, text: str) -> List[Dict[str, Any]]:
        if not text or not text.strip(): return []
        ast = self.md(text)
        return self._process_tokens(ast)

    def _process_tokens(self, tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocks = []
        if not tokens: return []
        
        for token in tokens:
            ttype = token.get("type")
            
            if ttype == "heading":
                notion_heading_level = min(max(token.get('attrs', {}).get('level', 1), 1), 3)
                blocks.append({
                    "object": "block",
                    "type": f"heading_{notion_heading_level}",
                    f"heading_{notion_heading_level}": {
                        "rich_text": self._parse_inline(token.get("children", []))
                    }
                })
            elif ttype in ["paragraph", "block_text"]:
                rt = self._parse_inline(token.get("children", []))
                if rt: blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt}})
            elif ttype == "list":
                ordered = token.get("attrs", {}).get("ordered", False)
                blocks.extend(self._process_list_items(token.get("children", []), ordered))
            elif ttype == "block_code":
                content = token.get("raw", "").strip()
                lang = token.get("attrs", {}).get("info", "plain text").split()[0] or "plain text"
                blocks.append({"object": "block", "type": "code", "code": {"language": lang, "rich_text": self._create_rich_text_objects(content, {"bold": False, "italic": False, "strikethrough": False, "code": False})}})
            elif ttype == "block_quote":
                rt = []
                for child in token.get("children", []):
                    rt.extend(self._parse_inline(child.get("children", [])) if "children" in child else self._parse_inline([child]))
                blocks.append({"object": "block", "type": "quote", "quote": {"rich_text": rt}})
            elif ttype == "thematic_break":
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            elif ttype == "table":
                blocks.append(self._process_table(token))
            elif ttype == "blank_line":
                continue
            else:
                rt = self._parse_inline(token.get("children", [])) if "children" in token else self._parse_inline([token])
                if rt: blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt}})
        
        return blocks

    def _process_list_items(self, items: List[Dict[str, Any]], ordered: bool) -> List[Dict[str, Any]]:
        blocks = []
        for item in items:
            attrs = item.get("attrs", {})
            is_task = (item.get("type") == "task_list_item") or ("checked" in attrs)
            children = item.get("children", [])
            
            # Find the title token (first non-blank)
            title_token = None
            for c in children:
                if c.get("type") != "blank_line":
                    title_token = c
                    break
            
            primary_rich_text = []
            nested_tokens = []
            
            if title_token:
                ttype = title_token.get("type")
                if ttype in ["paragraph", "block_text", "list_item_head"]:
                    primary_rich_text = self._parse_inline(title_token.get("children", []))
                    # Nested tokens are everything else
                    nested_tokens = [c for c in children if c != title_token and c.get("type") != "blank_line"]
                elif ttype in ["list", "block_code", "table", "block_quote"]:
                    # Starts directly with a nested block
                    primary_rich_text = []
                    nested_tokens = [c for c in children if c.get("type") != "blank_line"]
                else:
                    # Inline token
                    primary_rich_text = self._parse_inline([title_token])
                    nested_tokens = [c for c in children if c != title_token and c.get("type") != "blank_line"]

            block_type = "to_do" if is_task else ("numbered_list_item" if ordered else "bulleted_list_item")
            item_data = {"rich_text": primary_rich_text}
            if is_task: item_data["checked"] = attrs.get("checked", False)
            
            if nested_tokens:
                cb = self._process_tokens(nested_tokens)
                if cb: item_data["children"] = cb[:NOTION_BLOCK_CHILDREN_LIMIT]
                
            blocks.append({"object": "block", "type": block_type, block_type: item_data})
        return blocks

    def _process_table(self, token: Dict[str, Any]) -> Dict[str, Any]:
        """
        將 Mistune table AST 轉換為 Notion Table Block
        """
        sections = token.get("children", [])
        rows = []
        max_cols = 0
        has_column_header = False
        
        for section in sections:
            if section.get("type") == "table_head":
                has_column_header = True
                cells = [self._parse_inline(c.get("children", [])) for c in section.get("children", [])]
                if cells:
                    rows.append({"object": "block", "type": "table_row", "table_row": {"cells": cells}})
                    max_cols = max(max_cols, len(cells))
            else:
                for r in section.get("children", []):
                    cells = [self._parse_inline(c.get("children", [])) for c in r.get("children", [])]
                    if cells:
                        rows.append({"object": "block", "type": "table_row", "table_row": {"cells": cells}})
                        max_cols = max(max_cols, len(cells))
        
        return {
            "object": "block",
            "type": "table",
            "table": {
                "table_width": max_cols,
                "has_column_header": has_column_header,
                "has_row_header": False,
                "children": rows[:NOTION_BLOCK_CHILDREN_LIMIT]
            }
        }

    def _parse_inline(self, tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not tokens: return []
        return self._parse_inline_recursive(tokens, {"bold": False, "italic": False, "strikethrough": False, "code": False})

    def _parse_inline_recursive(self, tokens: List[Dict[str, Any]], style: Dict[str, bool]) -> List[Dict[str, Any]]:
        result = []
        for token in tokens:
            ttype = token.get("type")
            if ttype in ["text", "raw"]:
                txt = token.get("raw") or token.get("text") or token.get("content") or ""
                if txt: result.extend(self._create_rich_text_objects(txt, style))
            elif ttype == "strong":
                result.extend(self._parse_inline_recursive(token.get("children", []), {**style, "bold": True}))
            elif ttype == "emphasis":
                result.extend(self._parse_inline_recursive(token.get("children", []), {**style, "italic": True}))
            elif ttype == "strikethrough":
                result.extend(self._parse_inline_recursive(token.get("children", []), {**style, "strikethrough": True}))
            elif ttype == "codespan":
                txt = token.get("raw") or token.get("text") or token.get("content") or ""
                if txt: result.extend(self._create_rich_text_objects(txt, {**style, "code": True}))
            elif ttype == "link":
                url = token.get("attrs", {}).get("url", "")
                inner = self._parse_inline_recursive(token.get("children", []), style)
                for i in inner: 
                    if "text" in i: i["text"]["link"] = {"url": url}
                result.extend(inner)
            elif ttype in ["linebreak", "softbreak"]:
                result.extend(self._create_rich_text_objects("\n", style))
        return self._merge_rich_texts(result)

    def _create_rich_text_objects(self, text: str, style: Dict[str, bool]) -> List[Dict[str, Any]]:
        if not text: return []
        return [{"type": "text", "text": {"content": text[i:i+NOTION_RICH_TEXT_LIMIT]}, "annotations": style.copy()} for i in range(0, len(text), NOTION_RICH_TEXT_LIMIT)]

    def _merge_rich_texts(self, rich_texts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rich_texts: return []
        merged = []
        curr = rich_texts[0]
        for next_item in rich_texts[1:]:
            if curr.get("annotations") == next_item.get("annotations") and curr.get("text", {}).get("link") == next_item.get("text", {}).get("link") and (len(curr["text"]["content"]) + len(next_item["text"]["content"]) <= NOTION_RICH_TEXT_LIMIT):
                curr["text"]["content"] += next_item["text"]["content"]
            else:
                merged.append(curr); curr = next_item
        merged.append(curr)
        return merged
