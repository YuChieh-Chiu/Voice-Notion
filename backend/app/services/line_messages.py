"""純 LINE 文字呈現；不查詢來源、不保存內容，也不執行任何操作。"""

import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit

CAPTURE_ID = re.compile(r"^C[0-9a-f]{16}$")
FILE_ID = re.compile(r"^F[0-9a-f]{16}$")
ROLES = {"original": "原檔", "transcript": "逐字稿", "note": "筆記"}
CLEANUP_SCOPES = {
    "original": "指定紀錄的原始音檔（original）",
    "all": "指定紀錄的全部受管檔案（all）",
    "reviewed": "已校對語音的原始音檔（reviewed）",
    "file": "明確指定的檔案（file）",
    "files": "明確指定的檔案（files）",
}
STATES = {
    "pending": "等待處理",
    "processing": "處理中",
    "complete": "保存完成",
    "failed": "失敗",
    "delete_pending": "清理中",
    "deleted": "服務管理內容已移除",
    "local": "本機暫存",
    "allocated": "等待雲端上傳確認",
    "uploaded": "雲端已保存",
    "trash_pending": "等待清理",
    "trashed": "已移至垃圾桶",
    "missing": "檔案已不存在",
}


def one_line(value, limit=240):
    text = " ".join(
        "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(value)).split()
    )
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def private_link(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme in ("https", "http")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            if all(not unicodedata.category(c).startswith("C") for c in value):
                return value
    except ValueError:
        pass
    return ""


def message(
    event, status, *, capture_ids=(), content=(), files=(), links=(), next_steps=(), reminders=()
):
    lines = ["Note-Goblin｜" + one_line(event, 60)]
    for capture_id in dict.fromkeys(capture_ids):
        if isinstance(capture_id, str) and CAPTURE_ID.fullmatch(capture_id):
            lines.append("紀錄ID：" + capture_id)
    lines.append("狀態：" + one_line(status))
    for heading, values in (
        ("內容", content),
        ("檔案", files),
        ("連結", links),
        ("下一步", next_steps),
        ("必要提醒", reminders),
    ):
        # 清理警告不可裁切；清理預覽的整體長度由既有 guard 拒絕。
        limit = None if heading == "必要提醒" else 2300 if heading in ("檔案", "連結") else 512
        values = [one_line(value, limit) for value in values if value]
        if values:
            lines.extend(["", heading + "：", *values])
    return "\n".join(lines)


def file_description(file_id, *, capture_id=None, role=None, size=None, state=None):
    parts = []
    if isinstance(capture_id, str) and CAPTURE_ID.fullmatch(capture_id):
        parts.append("紀錄ID：" + capture_id)
    if isinstance(file_id, str) and FILE_ID.fullmatch(file_id):
        parts.append("檔案ID：" + file_id)
    if role is not None:
        parts.append("類型：" + ROLES.get(role, "其他") + "（" + one_line(role, 30) + "）")
    if size is not None:
        parts.append("大小：" + one_line(size, 24) + " bytes")
    if state is not None:
        parts.append("狀態：" + STATES.get(state, one_line(state, 50)))
    return "｜".join(parts)


def completion(
    title,
    url,
    *,
    capture_id=None,
    original_only=False,
    destination="drive",
    personal=True,
    reviewed=False,
):
    link = private_link(url)
    content = (["標題：" + one_line(title, 180)] if title else []) + [
        "目的地：" + ("Google Drive" if destination == "drive" else "Notion")
    ]
    next_steps = ["開啟連結查看保存內容。"] if link else ["請至保存目的地確認內容。"]
    if personal and isinstance(capture_id, str) and CAPTURE_ID.fullmatch(capture_id):
        next_steps = ["查看檔案與適用操作：", "/files " + capture_id]
    return message(
        "保存完成",
        "僅保存原檔，未解析"
        if original_only
        else "原文與摘要已保存，已人工校對"
        if reviewed
        else "原文與摘要已保存，尚未人工校對",
        capture_ids=[capture_id],
        content=content,
        links=[link],
        next_steps=next_steps,
        reminders=["雲端保存不代表本機已同步；請於 Google Drive 確認。"]
        if destination == "drive"
        else [],
    )


def partial(capture_id, *, personal=True, destination="drive"):
    return message(
        "部分保存",
        "原檔已保存至 Drive，文字處理尚未完成" if destination == "drive" else "文字處理尚未完成",
        capture_ids=[capture_id],
        next_steps=["查看目前檔案：", "/files " + capture_id]
        if personal and isinstance(capture_id, str) and CAPTURE_ID.fullmatch(capture_id)
        else ["請稍後查看保存結果。"],
        reminders=["目前不能視為完整筆記，請勿先清理原始來源。"],
    )


def capture_details(record, files):
    content = [
        "目的地：" + ("Google Drive" if record.destination == "drive" else "Notion"),
        "分類：" + one_line(record.category or "未分類", 64),
    ]
    content.append(
        "僅保存原檔，未解析"
        if record.kind in ("image", "file")
        else "人工校對：" + ("已確認完整原文與摘要" if record.reviewed_at else "尚未確認")
    )
    rows, links = [], []
    if record.destination == "notion":
        content.append("Notion 純文字保存，無附件。")
        if record.status not in ("delete_pending", "deleted"):
            links = [private_link(record.notion_url)]
    else:
        for file in files:
            rows.append(
                file_description(file.id, role=file.role, size=file.size_bytes, state=file.state)
            )
            if file.state == "uploaded" and record.status not in ("delete_pending", "deleted"):
                link = private_link(file.cloud_url)
                if link:
                    rows.append("連結（檔案ID：" + file.id + "）：" + link)
    return message(
        "檔案清單",
        STATES.get(record.status, one_line(record.status, 60)),
        capture_ids=[record.id],
        content=content,
        files=rows,
        links=links,
        next_steps=["輸入 /help 查看適用的管理指令。"],
    )


def review(record):
    next_steps = ["尚未清理任何檔案。需要管理指令時，輸入 /help。"]
    if record.destination == "drive" and record.kind == "audio":
        next_steps = ["如需預覽清理原始音檔：", "/clear " + record.id + " original"]
    return message(
        "校對已記錄",
        "已確認完整原文與摘要；尚未清理任何檔案。",
        capture_ids=[record.id],
        next_steps=next_steps,
    )


def cleanup_preview(preview, *, scope, filters=()):
    token = preview["token"]
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", token):
        raise ValueError("確認代號格式不正確，請重新預覽")
    expires = datetime.fromtimestamp(preview["expires_at"], timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    rows = [
        file_description(f["id"], capture_id=f["capture_id"], role=f["role"], size=f["bytes"])
        for f in preview["files"]
    ]
    # 服務內部警告與確切清單不可截斷；組完全文後由呼叫端的 4400 guard 決定是否取消。
    warning = " ".join(preview["warning"].split())
    return message(
        "清理預覽",
        "等待確認，尚未清理",
        capture_ids=[f["capture_id"] for f in preview["files"]],
        content=[
            "範圍：" + CLEANUP_SCOPES.get(scope, one_line(scope, 120)),
            "檔案數：" + str(preview["count"]),
            "合計：" + str(preview["total_bytes"]) + " bytes",
            "有效期限：" + expires,
            *filters,
        ],
        files=rows,
        next_steps=["確認：", "/confirm " + token, "取消：", "/cancel " + token],
        reminders=["確認只處理這份清單。", warning],
    )


def cleanup_result(outcome, *, token, capture_ids=()):
    rows = [file_description(f["file_id"], state=f["status"]) for f in outcome["files"]]
    errors = [
        "紀錄ID：" + item["capture_id"] + "｜來源狀態尚未更新。"
        for item in outcome["metadata_errors"]
        if CAPTURE_ID.fullmatch(item["capture_id"])
    ]
    next_steps = (
        ["需要其他管理指令時，輸入 /help。"]
        if outcome["complete"]
        else ["網路故障時，可重送本次確認以查詢或重試未完成項目：", "/confirm " + token]
    )
    reminders = [" ".join(outcome["message"].split())]
    if errors:
        reminders.append("網路故障可重試；若外部編輯衝突，需依部署指南人工調和，請勿持續重點確認。")
    return message(
        "清理結果",
        "清理完成" if outcome["complete"] else "部分清理未完成",
        capture_ids=capture_ids,
        content=errors,
        files=rows,
        next_steps=next_steps,
        reminders=reminders,
    )


def cleanup_cancel(status):
    descriptions = {
        "cancelled": "清單已取消；已執行的清理不會復原。",
        "done": "清單已執行；取消未套用，也不會復原內容。",
        "running": "清理已開始；取消未套用，已執行的項目不會復原。",
    }
    return message(
        "取消清理",
        descriptions.get(status, "確認清單狀態：" + one_line(status, 60)),
        next_steps=["查看紀錄現況，或輸入 /help 查看管理指令。"],
    )


def retraction(capture_id=None):
    return message(
        "訊息收回",
        "服務管理內容已撤下。" if capture_id else "已停止該訊息後續收件，未找到對應紀錄。",
        capture_ids=[capture_id],
        next_steps=["需要管理其他紀錄時，輸入 /help。"],
        reminders=["另存副本、Atlas 衍生內容與供應商備份不在此範圍。"],
    )


def operation_error(reason):
    return message(
        "操作未完成", one_line(reason, 300), next_steps=["輸入 /help 查看可用指令與適用條件。"]
    )
