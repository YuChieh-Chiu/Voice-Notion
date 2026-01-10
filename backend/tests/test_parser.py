
from app.utils.markdown_parser import NotionMarkdownParser
import json

def test_parser():
    parser = NotionMarkdownParser()
    
    test_cases = [
        {
            "name": "Nested Lists",
            "md": "- Item 1\n  - Subitem 1.1\n  - Subitem 1.2\n- Item 2"
        },
        {
            "name": "Table",
            "md": "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |\n| Cell 3 | Cell 4 |"
        },
        {
            "name": "Task List",
            "md": "- [x] Done task\n- [ ] Pending task"
        }
    ]
    
    for case in test_cases:
        print(f"--- Case: {case['name']} ---")
        ast = parser.md(case["md"])
        print("AST:", json.dumps(ast, indent=2))
        blocks = parser.parse(case["md"])
        print("Blocks:", json.dumps(blocks, indent=2, ensure_ascii=False))
        print("\n")

if __name__ == "__main__":
    test_parser()
