"""連線測試（拋棄式）：確認雲端環境能否取得台股開盤統整所需的資料來源。

用法：python spike/probe.py
選用環境變數：
  GEMINI_API_KEY  有設定才測試 Gemini 讀 YouTube 影片
  GEMINI_MODEL    預設 gemini-2.5-flash
只用標準函式庫，不需安裝套件。金鑰不會被印出。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CHANNEL_ID = "UCf0WClSgplv2lJ2X3OeI5YA"
UA = "tw-open-report-probe/0.1"
results = []


def fetch(url, data=None, headers=None, timeout=30):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def check(name, fn):
    try:
        detail = fn()
        results.append((name, "OK", detail))
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        results.append((name, "FAIL", f"HTTP {e.code}: {body}"))
    except Exception as e:  # noqa: BLE001 - 測試腳本，全部記錄
        results.append((name, "FAIL", f"{type(e).__name__}: {e}"))


def mis():
    ch = "|".join(f"tse_{c}.tw" for c in ("t00", "0050", "00631L", "00685L"))
    url = ("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch="
           + urllib.parse.quote(ch))
    _, body = fetch(url)
    d = json.loads(body)
    rows = d.get("msgArray") or []
    if not rows:
        raise RuntimeError(f"沒有資料: {str(d)[:200]}")
    return "; ".join(
        f"{m.get('c')} 日期={m.get('d')} 今開={m.get('o')} 昨收={m.get('y')} 最新={m.get('z')}"
        for m in rows)


def openapi():
    _, body = fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
    rows = [r for r in json.loads(body) if r.get("Code") in ("0050", "00631L", "00685L")]
    return "; ".join(f"{r['Code']} {r.get('Date')} 收盤={r.get('ClosingPrice')}" for r in rows)


def taifex():
    status, body = fetch("https://www.taifex.com.tw/cht/3/futDailyMarketReport")
    return f"HTTP {status}, {len(body)} bytes"


latest_video = {}


def yt_rss():
    _, body = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}")
    text = body.decode("utf-8", "replace")
    ids = re.findall(r"<yt:videoId>([^<]+)", text)
    titles = re.findall(r"<media:title>([^<]+)", text)
    if not ids:
        raise RuntimeError("RSS 沒有影片")
    latest_video["id"], latest_video["title"] = ids[0], titles[0] if titles else ""
    return " | ".join(f"{i}: {t}" for i, t in list(zip(ids, titles))[:3])


def _remember(vid, title):
    if "id" not in latest_video:
        latest_video["id"], latest_video["title"] = vid, title


def yt_streams_page():
    _, body = fetch(f"https://www.youtube.com/channel/{CHANNEL_ID}/streams",
                    headers={"Accept-Language": "zh-TW"})
    text = body.decode("utf-8", "replace")
    out, seen = [], set()
    # 標題格式如 2026/11/16(一)...【早晨財經速解讀】；往前找最近的影片代號
    for m in re.finditer(r'(20\d\d/\d{1,2}/\d{1,2}\(.\)[^"\\]{3,150})', text):
        before = text[max(0, m.start() - 3000):m.start()]
        ids = re.findall(r'"(?:videoId|contentId)":"([\w-]{11})"', before)
        if ids and ids[-1] not in seen:
            seen.add(ids[-1])
            out.append((ids[-1], m.group(1)))
    if not out:
        n_ids = len(set(re.findall(r'"(?:videoId|contentId)":"([\w-]{11})"', text)))
        raise RuntimeError(f"找不到日期標題（{len(body)} bytes, 影片代號數={n_ids}, "
                           f"含早晨財經={'早晨財經' in text}, consent={'consent.youtube' in text})")
    _remember(*out[0])
    return " | ".join(f"{v}: {t}" for v, t in out[:3])


def yt_live_page():
    _, body = fetch(f"https://www.youtube.com/channel/{CHANNEL_ID}/live",
                    headers={"Accept-Language": "zh-TW"})
    text = body.decode("utf-8", "replace")
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})', text)
    t = re.search(r"<title>([^<]*)</title>", text)
    if not m:
        raise RuntimeError(f"沒有指向影片（{len(body)} bytes）")
    _remember(m.group(1), t.group(1) if t else "")
    return f"{m.group(1)}: {t.group(1) if t else ''}"


def gemini_models():
    _, body = fetch("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                    headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    names = [m["name"].split("/")[-1] for m in json.loads(body).get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and "image" not in n and "tts" not in n
             and "live" not in n and "lite" not in n and "preview" not in n and "exp" not in n]
    if not os.environ.get("GEMINI_MODEL"):
        os.environ["GEMINI_MODEL"] = "gemini-flash-latest"
    return f"選用={os.environ.get('GEMINI_MODEL')} / 可用 flash 系列={sorted(flash)}"


def yt_watch():
    status, body = fetch(f"https://www.youtube.com/watch?v={latest_video['id']}",
                         headers={"Accept-Language": "zh-TW"})
    text = body.decode("utf-8", "replace")
    return (f"HTTP {status}, {len(body)} bytes, 含字幕軌={'captionTracks' in text}, "
            f"要求登入={'Sign in to confirm' in text}")


def gemini():
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    payload = {"contents": [{"parts": [
        {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={latest_video['id']}"}},
        {"text": "用繁體中文三句話說明這支影片的主要內容，並指出影片中提到的一個具體數字。"},
    ]}]}
    _, body = fetch(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        timeout=300)
    d = json.loads(body)
    text = d["candidates"][0]["content"]["parts"][0]["text"]
    return f"影片={latest_video['title']} / 模型={model} / 回覆={text[:400]}"


check("證交所即時報價 MIS", mis)
check("證交所 OpenAPI 前日收盤", openapi)
check("期交所行情表", taifex)
check("YouTube 頻道 RSS", yt_rss)
check("YouTube 頻道直播清單頁", yt_streams_page)
check("YouTube 頻道 /live 頁", yt_live_page)
if os.environ.get("GEMINI_API_KEY"):
    check("Gemini 可用模型", gemini_models)
if latest_video:
    check("YouTube 影片頁", yt_watch)
    if os.environ.get("GEMINI_API_KEY"):
        check("Gemini 讀 YouTube 影片", gemini)
    else:
        results.append(("Gemini 讀 YouTube 影片", "SKIP", "未設定 GEMINI_API_KEY"))

for name, status, detail in results:
    print(f"[{status}] {name}\n      {detail}\n")
sys.exit(0)
