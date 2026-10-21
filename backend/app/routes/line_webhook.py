"""先驗原始 body 簽章，再接受白名單使用者的私人事件。"""

import base64
import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.services.capture_repository import CaptureRepository
from app.services.line_events import LineEventStore
from app.worker.tasks import process_line_event

settings = get_settings()
router = APIRouter(prefix="/api/v1/line", tags=["LINE"])


@router.post("/webhook")
async def webhook(request: Request):
    if not settings.LINE_CHANNEL_SECRET or not settings.LINE_USER_ID:
        raise HTTPException(503, "LINE webhook is not configured")
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise HTTPException(413, "Webhook exceeds limit")
    expected = base64.b64encode(
        hmac.digest(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256)
    ).decode()
    signature = request.headers.get("x-line-signature", "")
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise HTTPException(401, "Invalid LINE signature")
    try:
        payload = json.loads(body)
        events = payload["events"]
        if not isinstance(events, list) or len(events) > 100:
            raise ValueError("Invalid event collection")
        store = LineEventStore(CaptureRepository(settings.CAPTURE_DATA_DIR))
        for event in events:
            source = event.get("source", {})
            if source.get("type") != "user" or source.get("userId") != settings.LINE_USER_ID:
                continue
            if event.get("type") not in ("message", "postback"):
                continue
            event_id = store.accept(event)
            if event_id:
                process_line_event.delay(event_id)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(400, "Invalid LINE payload") from None
    except Exception:
        raise HTTPException(503, "Event saved if accepted; retry webhook delivery") from None
    return {"status": "ok"}
