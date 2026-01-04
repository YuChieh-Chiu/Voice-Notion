import requests
import time
import argparse
import sys
import os

def smoke_test(file_path, api_url):
    print(f"🚀 Starting smoke test with file: {file_path}")
    
    if not os.path.exists(file_path):
        print(f"❌ Error: File not found at {file_path}")
        return

    # 1. Upload audio
    with open(file_path, 'rb') as f:
        files = {'audio': f}
        print(f"📤 Uploading to {api_url}/api/v1/note...")
        try:
            response = requests.post(f"{api_url}/api/v1/note", files=files)
            response.raise_for_status()
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            return

    data = response.json()
    task_id = data.get("task_id")
    print(f"✅ Upload successful! Task ID: {task_id}")
    print(f"⏳ Waiting for processing (Check worker logs for progress)...")
    
    # 目前 API 沒有實作 GET /task/{id}，所以這裡純粹是模擬等待
    # 使用者需要手動查看 Docker 日誌或 Notion 頁面
    time.sleep(5)
    print("\n💡 Tip: Run `docker-compose logs -f worker` to see the processing details.")
    print("Check your Notion and Line to verify the result!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Voice-Notion Smoke Test Script")
    parser.add_argument("--file", required=True, help="Path to the audio file")
    parser.add_argument("--url", default="http://localhost:8000", help="Backend API URL")
    
    args = parser.parse_args()
    smoke_test(args.file, args.url)
