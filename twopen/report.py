"""資料檔 latest.json 的結構與寫檔。"""
import json
from datetime import timedelta, timezone

# 台灣沒有日光節約時間，用固定時差即可（Windows 沒有內建時區資料庫）。
TPE = timezone(timedelta(hours=8))


def _stamp(moment):
    return moment.astimezone(TPE).isoformat(timespec="seconds")


def pending_live():
    return {"status": "pending", "title": None, "url": None, "description": None,
            "summary": None, "updated_at": None}


def live_result(status, video, summary, now):
    video = video or {}
    return {"status": status, "title": video.get("title"), "url": video.get("url"),
            "description": video.get("description"), "summary": summary,
            "updated_at": _stamp(now)}


def build_document(now, market_status, quotes, live, error=None):
    return {"generated_at": _stamp(now),
            "trade_date": now.astimezone(TPE).date().isoformat(),
            "market_status": market_status, "quotes": quotes, "live": live, "error": error}


def write_document(doc, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
