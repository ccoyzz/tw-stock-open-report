"""游庭皓的財經皓角：找當天直播、判斷是否結束、請 Gemini 整理重點。"""
import json
import re
import urllib.parse

from .http import get_json

CHANNEL_ID = "UC0lbAQVpenvfA2QqzsRtL_g"
UPLOADS_PLAYLIST = "UU0lbAQVpenvfA2QqzsRtL_g"
GEMINI_MODEL = "gemini-flash-latest"
UNREADABLE = "無法讀取影片內容"

SUMMARY_PROMPT = (
    "這是財經節目「游庭皓的財經皓角」當天的早晨直播。請用繁體中文、用你自己的話整理主講人的觀點，"
    "不要大段引用原話。請分項寫出：\n"
    "1. 對美股的看法\n2. 對台股的看法\n3. 總體經濟與利率、匯率\n"
    "4. 提到的產業或個股\n5. 他提醒的風險與接下來的觀察重點\n"
    "請保留他提到的具體數字。某一項影片沒有談到就寫「未提及」。"
    "只根據影片實際內容整理，不要推測、不要補充影片以外的資訊。"
    f"如果你無法讀取影片內容，只回覆「{UNREADABLE}」這幾個字。"
)

_YT = "https://www.googleapis.com/youtube/v3/"
_GEMINI = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent")


def title_matches_date(title, day):
    if not isinstance(title, str):
        return False
    pattern = rf"(?<!\d){day.year}/0?{day.month}/0?{day.day}(?!\d)"
    return re.search(pattern, title) is not None


def _items(response):
    items = response.get("items") if isinstance(response, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def pick_today_video(playlist_response, day):
    for item in _items(playlist_response):
        snippet = item.get("snippet")
        if not isinstance(snippet, dict):
            continue
        resource = snippet.get("resourceId")
        video_id = resource.get("videoId") if isinstance(resource, dict) else None
        title = snippet.get("title")
        if video_id and title_matches_date(title, day):
            return {"id": video_id, "title": title,
                    "description": snippet.get("description") or "",
                    "url": f"https://www.youtube.com/watch?v={video_id}"}
    return None


def is_finished(videos_response):
    items = _items(videos_response)
    if not items:
        return False
    snippet = items[0].get("snippet") or {}
    details = items[0].get("liveStreamingDetails") or {}
    return snippet.get("liveBroadcastContent") == "none" or bool(details.get("actualEndTime"))


def extract_summary(gemini_response):
    try:
        parts = gemini_response["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    if not text or UNREADABLE in text:
        return None
    return text


def find_today_video(day, api_key):
    url = (_YT + "playlistItems?part=snippet&maxResults=10"
           f"&playlistId={UPLOADS_PLAYLIST}&key={urllib.parse.quote(api_key)}")
    return pick_today_video(get_json(url, label="YouTube影片清單"), day)


def video_finished(video_id, api_key):
    url = (_YT + "videos?part=snippet,liveStreamingDetails"
           f"&id={urllib.parse.quote(video_id)}&key={urllib.parse.quote(api_key)}")
    return is_finished(get_json(url, label="YouTube影片狀態"))


def summarize(video_url, api_key):
    payload = {"contents": [{"parts": [{"file_data": {"file_uri": video_url}},
                                       {"text": SUMMARY_PROMPT}]}]}
    response = get_json(_GEMINI, label="Gemini摘要", data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json",
                                 "x-goog-api-key": api_key},
                        timeout=300)
    return extract_summary(response)
