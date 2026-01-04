"""
Notion Integration Verification Script
手動驗證腳本 - 需實際 Notion API 連線

測試項目：
1. Sync Page Tree (Cache & Structure)
2. Create Subpage (Markdown Rendering)
3. Append to Page (Markdown Rendering)

執行方式：
    cd backend
    poetry run python -m scripts.verify_notion_integration

注意：此腳本會在真實 Notion 環境建立測試頁面
"""
import sys
import os
from pathlib import Path

# Add parent directory to path for app imports
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

import logging
import time
from app.services.notion_service import NotionService

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_notion_integration():
    service = NotionService()
    
    print("\n--- 1. Testing Sync Page Tree ---")
    tree = service.sync_page_tree()
    print("Roots:")
    for root in tree["roots"]:
        print(f"  - {root['title']} ({root['id']})")
    print("Subpages:")
    for sub in tree["subpages"]:
        print(f"  - {sub['title']} ({sub['id']}) (Parent: {sub['parent_id']})")
        
    if not tree["roots"]:
        print("❌ No roots found. Cannot proceed with Create/Append test.")
        return

    root_id = tree["roots"][0]["id"]
    print(f"\nUsing Root: {tree['roots'][0]['title']} ({root_id})")
    
    print("\n--- 2. Testing Create Subpage ---")
    test_title = "Test Create - Markdown Support"
    test_md = """
# Heading 1
## Heading 2
### Heading 3

- Bullet List Item 1
- **Bold Item** 2
- *Italic Item* 3

1. Ordered List 1
2. Ordered List 2

> This is a blockquote.
> Multiple lines in quote.

---

    # This is an indented code block
    print("Hello Indented")

```python
def hello():
    print("Hello Fenced")
```

[Link to Google](https://google.com)

---

[**Bold Link**](https://example.com)

---

## Long Text Test (2000+ chars should be split)

{long_text}

---

End of test.
    """.format(long_text="A" * 2100)
    
    try:
        result = service.create_subpage(root_id, test_title, test_md)
        page_url = result["url"]
        page_id = result["id"]
        print(f"✅ Created Page: {page_url}")
        print(f"   Page ID: {page_id}")
        
        print("\n--- 2.1 Verify Cache Update ---")
        # Now we call sync_page_tree again. 
        # Since we just updated the cache, it should return the new page IMMEDIATELY
        # without hitting the Notion API (or even if it did, it might miss it, but cache has it!).
        tree_check = service.sync_page_tree()
        found_in_cache = any(p["id"] == page_id for p in tree_check["subpages"])
        
        if found_in_cache:
            print("✅ Cache Verification Passed: New page found in sync_page_tree() immediately.")
        else:
            print("❌ Cache Verification Failed: New page NOT found in sync_page_tree().")
        
        print("\n--- 3. Testing Append to Page ---")
        # 直接使用剛建立的 page_id，不需要等待索引
        print(f"Appending to page ID: {page_id}")
        
        append_url = service.append_to_page(
            page_id,
            "Test Append Section",
            "Appended content with **Bold** and *Italic*."
        )
        print(f"✅ Appended to Page: {append_url}")
            
    except Exception as e:
        print(f"❌ Operation Failed: {e}")

if __name__ == "__main__":
    test_notion_integration()
