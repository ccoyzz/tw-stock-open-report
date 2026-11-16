# 台股開盤資料抓取器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每個台股交易日在 GitHub Actions 上抓開盤數據與游庭皓直播重點，發佈成公開的 `latest.json`，供 Claude 排程任務讀取。

**Architecture:** 一支 Python 程式分兩階段執行：09:00:30 後查證交所即時報價寫出開盤數據；直播結束後用 YouTube Data API 找到影片、交給 Gemini 摘要。每次狀態改變就把 `latest.json` 推到儲存庫的 `data` 分支。時間、睡眠、網路、發佈都以參數注入，讓流程可以不連網測試。

**Tech Stack:** Python 3.12 標準函式庫（`urllib`、`json`、`decimal`、`datetime`、`unittest`）、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-11-16-tw-open-data-design.md`

## Global Constraints

- 只用 Python 標準函式庫，不新增任何相依套件。測試用 `python -m unittest discover -s tests -v`。
- 所有時間判斷用台灣時間。台灣沒有日光節約時間，一律用固定時差 `timezone(timedelta(hours=8))`，常數名 `TPE`。**不用 `zoneinfo`**：Windows 沒有內建時區資料，會讓本機測試失敗（這點與設計文件 5.5.1 的寫法不同，效果相同）。不得使用執行環境的本地時間。
- 數字在資料檔中一律是字串，用 `decimal.Decimal` 計算，價格與漲跌固定兩位小數，漲跌幅兩位小數（`ROUND_HALF_UP`）。正數帶 `+`，零為 `0.00`。
- 拿不到的值是 `null` 並帶 `status` 與 `reason`，絕不填猜測值。
- 任何例外訊息、紀錄、資料檔都不得包含請求網址或金鑰（YouTube 金鑰在網址參數裡）。
- 金鑰只從環境變數 `GEMINI_API_KEY`、`YOUTUBE_API_KEY` 讀取。
- 頻道代號 `UC0lbAQVpenvfA2QqzsRtL_g`，上傳清單 `UU0lbAQVpenvfA2QqzsRtL_g`，Gemini 模型 `gemini-flash-latest`。
- 時間點：開始查價 09:00:30；查價截止 09:10；查價重試間隔 20 秒；直播檢查間隔 60 秒；Gemini 重試間隔 120 秒；直播截止 10:00。
- 提交訊息結尾加 `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`。

## Review Focus

1. 即時報價回應殘缺（`msgArray` 為空或缺少、欄位是 `-`、缺鍵、非數字）→ 不當機，該標的 `status="missing"`。測試在 Task 2。
2. 工作流程延遲啟動（09:10 之後、甚至 10:00 之後才開始）→ 不做負數睡眠，立刻查一次並照常產出。測試在 Task 5。
3. 標題日期比對：`2026/11/1` 不得誤配到 `2026/11/16` 的影片；`2026/11/06` 與 `2026/11/6` 都要配得到。測試在 Task 3。
4. 執行中途任何例外 → 仍要發佈帶今天日期的資料檔，`error` 欄有訊息且不含網址。測試在 Task 5（流程）與 Task 1（訊息）。
5. Gemini 回應沒有候選、被擋或文字為空 → 直播狀態為 `title_only`，不當機。測試在 Task 3 與 Task 5。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `twopen/__init__.py` | 空檔 |
| `twopen/http.py` | HTTP 請求與不洩漏網址的錯誤型別 |
| `twopen/quotes.py` | 即時報價與官方收盤的抓取、解析、漲跌計算 |
| `twopen/live.py` | 找當天直播、判斷是否結束、Gemini 摘要 |
| `twopen/report.py` | 組出資料檔結構、寫檔 |
| `twopen/run.py` | 兩階段流程與進入點 |
| `tests/test_*.py` | 各模組的離線測試 |
| `.github/workflows/daily.yml` | 排程與發佈 |
| `docs/claude-task-prompt.md` | 新版 Claude 排程任務提示詞 |

刪除：`spike/`、`.github/workflows/probe.yml`。

---

### Task 1: HTTP 輔助與專案骨架

**Files:**
- Create: `twopen/__init__.py`, `twopen/http.py`, `tests/__init__.py`, `tests/test_http.py`

**Interfaces:**
- Produces:
  - `class FetchError(Exception)`：訊息格式 `"<label>: <原因>"`，不含網址。
  - `get_json(url: str, *, label: str, headers: dict[str, str] | None = None, data: bytes | None = None, timeout: float = 30, opener=urllib.request.urlopen) -> object`：成功回傳解析後的 JSON；HTTP 錯誤、連線錯誤、逾時、JSON 解析失敗一律丟 `FetchError`。送出 `User-Agent: tw-stock-open-report/1.0`。

- [ ] **Step 1: 寫失敗的測試** `tests/test_http.py`

```python
SECRET_URL = "https://example.test/x?key=SECRETKEY"

def fake_opener(payload=b'{"a": 1}', exc=None):
    def opener(req, timeout=None):
        if exc: raise exc
        return io.BytesIO(payload)   # 需支援 with：包一層 contextlib.closing 或自訂類別
    return opener

def test_returns_parsed_json(self):
    self.assertEqual(get_json(SECRET_URL, label="測試", opener=fake_opener()), {"a": 1})

def test_http_error_message_has_label_and_code_but_no_url(self):
    err = urllib.error.HTTPError(SECRET_URL, 403, "Forbidden", {}, io.BytesIO(b""))
    with self.assertRaises(FetchError) as cm:
        get_json(SECRET_URL, label="即時報價", opener=fake_opener(exc=err))
    self.assertIn("即時報價", str(cm.exception)); self.assertIn("403", str(cm.exception))
    self.assertNotIn("SECRETKEY", str(cm.exception)); self.assertNotIn("example.test", str(cm.exception))

def test_url_error_and_timeout_become_fetch_error_without_url(self):   # URLError(SECRET_URL)、TimeoutError 各一
def test_invalid_json_becomes_fetch_error(self):                       # payload=b"<html>"
```

- [ ] **Step 2:** `python -m unittest discover -s tests -v` → 失敗（模組不存在）
- [ ] **Step 3:** 實作 `twopen/http.py`。`FetchError` 的原因只取狀態碼或例外類別名稱，不得用 `str(原例外)`（`URLError` 的字串可能帶網址）。
- [ ] **Step 4:** 測試通過
- [ ] **Step 5:** Commit `feat: add http helper that never leaks urls`

---

### Task 2: 開盤數據（`quotes.py`）

**Files:**
- Create: `twopen/quotes.py`, `tests/test_quotes.py`

**Interfaces:**
- Consumes: `get_json`, `FetchError`
- Produces:
  - `SYMBOLS: list[tuple[str, str, str]]` =
    `[("台灣加權指數","TAIEX","tse_t00.tw"), ("元大台灣50","0050","tse_0050.tw"), ("元大台灣50正2","00631L","tse_00631L.tw"), ("群益臺灣加權正2","00685L","tse_00685L.tw")]`
  - `@dataclass(frozen=True) class MisRow: code: str; day: date | None; open: Decimal | None; prev_close: Decimal | None`（`code` 是 `SYMBOLS` 第二欄；即時報價的 `c` 為 `t00` 時對應 `TAIEX`）
  - `parse_mis(payload: object) -> dict[str, MisRow]`
  - `index_is_today(rows: dict[str, MisRow], today: date) -> bool`
  - `all_open(rows: dict[str, MisRow], today: date) -> bool`：四個標的都有、`day == today`、`open` 不為 `None`
  - `parse_official(rows: object) -> dict[str, Decimal]`：從 `STOCK_DAY_ALL` 取三檔 ETF 的 `ClosingPrice`
  - `compute_change(open_: Decimal, prev_close: Decimal) -> tuple[str, str]`：回傳 `(漲跌, 漲跌幅)`
  - `build_quotes(rows: dict[str, MisRow], official: dict[str, Decimal], today: date) -> list[dict]`：依 `SYMBOLS` 順序，每筆鍵為 `name, code, status, reason, prev_close, open, change, change_pct, official_prev_close, prev_close_mismatch, source`
  - `fetch_mis() -> object`（`label="證交所即時報價"`）、`fetch_official() -> object`（`label="證交所OpenAPI"`）

- [ ] **Step 1: 寫失敗的測試**

樣本（放在測試檔內）：實測回應的精簡版
```python
MIS = {"msgArray": [
  {"c": "t00",    "d": "20261116", "o": "43512.42", "y": "43675.12", "z": "42288.39"},
  {"c": "0050",   "d": "20261116", "o": "101.2000", "y": "102.5000", "z": "-"},
  {"c": "00631L", "d": "20261116", "o": "31.2500",  "y": "31.9200",  "z": "-"},
  {"c": "00685L", "d": "20261116", "o": "10.1400",  "y": "10.4000",  "z": "-"}]}
OFFICIAL = [{"Code": "0050", "ClosingPrice": "102.50"}, {"Code": "00631L", "ClosingPrice": "31.92"},
            {"Code": "00685L", "ClosingPrice": "10.40"}, {"Code": "2330", "ClosingPrice": "2350.00"}]
TODAY = date(2026, 11, 16)
```
測試與斷言：
```python
test_parse_mis_maps_index_and_etfs:   rows["TAIEX"].open == Decimal("43512.42"); rows["0050"].day == TODAY; len(rows) == 4
test_compute_change_matches_spec:     compute_change(Decimal("43512.42"), Decimal("43675.12")) == ("-162.70", "-0.37")
test_compute_change_etf:              compute_change(Decimal("10.14"), Decimal("10.40")) == ("-0.26", "-2.50")
test_compute_change_positive_and_zero: (Decimal("103"), Decimal("100")) -> ("+3.00", "+3.00"); 相等 -> ("0.00", "0.00")
test_build_quotes_ok:                 四筆、順序同 SYMBOLS、status 全為 "ok"；
                                      TAIEX: prev_close "43675.12", open "43512.42", change "-162.70", change_pct "-0.37", official_prev_close None
                                      0050: open "101.20", official_prev_close "102.50", prev_close_mismatch False, source "證交所即時報價"
test_build_quotes_flags_mismatch:     official 的 0050 改成 Decimal("100.95") -> prev_close_mismatch True，兩個值都保留
test_dash_open_is_missing:            0050 的 "o" 改 "-" -> status "missing"、open None、change None、reason 非空；all_open False
test_stale_date_is_missing:           00631L 的 "d" 改 "20261113" -> 該筆 status "missing"、open None
test_index_is_today:                  正常 True；t00 的 "d" 改 "20261113" -> False
test_malformed_payloads_do_not_raise: parse_mis({})、parse_mis({"msgArray": []})、parse_mis(None)、
                                      parse_mis({"msgArray": [{"c": "0050", "o": "abc"}]}) 皆不丟例外；
                                      build_quotes({}, {}, TODAY) 回傳四筆 status "missing"
test_parse_official_ignores_bad_rows: parse_official(OFFICIAL) == {三檔}；parse_official(None) == {}；ClosingPrice 為 "" 的列略過
```

- [ ] **Step 2:** 跑測試 → 失敗
- [ ] **Step 3:** 實作。即時報價網址：`https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch=` 加上以 `|` 串接並 URL 編碼的代號。官方收盤：`https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`。`open` 或 `prev_close` 為 0 或負數視同缺值。
- [ ] **Step 4:** 測試通過
- [ ] **Step 5:** Commit `feat: parse live quotes and compute opening change`

---

### Task 3: 直播（`live.py`）

**Files:**
- Create: `twopen/live.py`, `tests/test_live.py`

**Interfaces:**
- Consumes: `get_json`, `FetchError`
- Produces:
  - 常數 `CHANNEL_ID`、`UPLOADS_PLAYLIST`、`GEMINI_MODEL`（值見 Global Constraints）、`SUMMARY_PROMPT: str`
  - `title_matches_date(title: str, day: date) -> bool`
  - `pick_today_video(playlist_response: object, day: date) -> dict | None`：回傳 `{"id": str, "title": str, "description": str, "url": "https://www.youtube.com/watch?v=<id>"}`
  - `is_finished(videos_response: object) -> bool`
  - `extract_summary(gemini_response: object) -> str | None`：沒有可用文字時回傳 `None`
  - `find_today_video(day: date, api_key: str) -> dict | None`（`label="YouTube影片清單"`）
  - `video_finished(video_id: str, api_key: str) -> bool`（`label="YouTube影片狀態"`）
  - `summarize(video_url: str, api_key: str) -> str | None`（`label="Gemini摘要"`，`timeout=300`）

- [ ] **Step 1: 寫失敗的測試**

```python
T16 = "2026/11/16(一)輝達崩跌 散戶狂抄底【早晨財經速解讀】"
T13 = "2026/11/13(五)黃仁勳退位【早晨財經速解讀】"
def playlist(*pairs):  # (id, title) -> {"items": [{"snippet": {"title": t, "description": "說明", "resourceId": {"videoId": i}}}]}

test_title_matches_exact_day:        title_matches_date(T16, date(2026,11,16)) True；date(2026,11,13) False
test_single_digit_day_not_prefix:    title_matches_date(T16, date(2026,11,1)) False
test_zero_padded_and_unpadded:       "2026/11/06(五)…" 與 "2026/11/6(五)…" 對 date(2026,11,6) 皆 True；"2026/1/5(一)…" 與 "2026/01/05(一)…" 對 date(2026,1,5) 皆 True
test_title_with_leading_text:        "【直播】2026/11/16(一)…" -> True（日期不必在最前面）
test_pick_today_video:               pick_today_video(playlist(("a",T16),("b",T13)), date(2026,11,16)) == {"id":"a","title":T16,"description":"說明","url":"https://www.youtube.com/watch?v=a"}
test_pick_returns_none:              只有 T13 時回傳 None；輸入 {}、None、{"items":[{}]} 回傳 None 不丟例外
test_is_finished:                    {"items":[{"snippet":{"liveBroadcastContent":"none"}}]} True
                                     {"items":[{"snippet":{"liveBroadcastContent":"live"},"liveStreamingDetails":{}}]} False
                                     {"items":[{"snippet":{"liveBroadcastContent":"live"},"liveStreamingDetails":{"actualEndTime":"2026-11-16T01:05:00Z"}}]} True
                                     {"items":[{"snippet":{"liveBroadcastContent":"upcoming"}}]} False；{}、{"items":[]} False
test_extract_summary:                {"candidates":[{"content":{"parts":[{"text":"重點一"},{"text":"重點二"}]}}]} -> "重點一重點二"
test_extract_summary_empty_cases:    {}、{"candidates":[]}、{"candidates":[{"finishReason":"SAFETY"}]}、parts 文字全為空白 -> None
test_prompt_forbids_guessing:        "繁體中文" in SUMMARY_PROMPT and "不要推測" in SUMMARY_PROMPT
```

- [ ] **Step 2:** 跑測試 → 失敗
- [ ] **Step 3:** 實作。
  - 日期比對用正規表示式 `(?<!\d)YYYY/0?M/0?D(?!\d)`。
  - YouTube：`https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=10&playlistId=…&key=…` 與 `…/videos?part=snippet,liveStreamingDetails&id=…&key=…`。
  - Gemini：`POST https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent`，標頭 `x-goog-api-key`，本文 `{"contents":[{"parts":[{"file_data":{"file_uri": video_url}},{"text": SUMMARY_PROMPT}]}]}`。
  - `SUMMARY_PROMPT` 要求：繁體中文；用自己的話整理，不大段引用；分項寫出對美股、台股、總經、產業或個股的看法，以及提到的風險與觀察重點；保留他提到的具體數字；只根據影片內容，不要推測；影片內容無法讀取時只回覆「無法讀取影片內容」。
  - `extract_summary` 遇到文字含「無法讀取影片內容」也回傳 `None`（加一個對應測試）。
- [ ] **Step 4:** 測試通過
- [ ] **Step 5:** Commit `feat: find today's livestream and summarize it`

---

### Task 4: 資料檔（`report.py`）

**Files:**
- Create: `twopen/report.py`, `tests/test_report.py`

**Interfaces:**
- Produces:
  - `TPE = timezone(timedelta(hours=8))`
  - `pending_live() -> dict`：`{"status":"pending","title":None,"url":None,"description":None,"summary":None,"updated_at":None}`
  - `live_result(status: str, video: dict | None, summary: str | None, now: datetime) -> dict`：`status` 為 `ok`／`title_only`／`not_found`；`updated_at` 為 `now` 的 ISO 字串（秒精度、`+08:00`）
  - `build_document(now: datetime, market_status: str, quotes: list[dict], live: dict, error: str | None = None) -> dict`：鍵 `generated_at, trade_date, market_status, quotes, live, error`；`market_status` 為 `open`／`closed`／`error`
  - `write_document(doc: dict, path: pathlib.Path) -> None`：UTF-8、`ensure_ascii=False`、縮排 2、結尾換行，自動建立上層目錄

- [ ] **Step 1: 寫失敗的測試**

```python
NOW = datetime(2026, 11, 16, 9, 1, 12, tzinfo=TPE)
test_build_document_fields:   doc = build_document(NOW, "open", [], pending_live())
                              doc["generated_at"] == "2026-11-16T09:01:12+08:00"; doc["trade_date"] == "2026-11-16"; doc["error"] is None
test_trade_date_uses_taipei:  now = datetime(2026,11,16,0,30,tzinfo=timezone.utc)（台灣 08:30）-> trade_date "2026-11-16"
                              now = datetime(2026,11,15,23,0,tzinfo=timezone.utc)（台灣隔天 07:00）-> trade_date "2026-11-16"，generated_at 以 +08:00 結尾
test_live_result_ok:          live_result("ok", {"id":"a","title":"T","description":"D","url":"U"}, "S", NOW) ==
                              {"status":"ok","title":"T","url":"U","description":"D","summary":"S","updated_at":"2026-11-16T09:01:12+08:00"}
test_live_result_not_found:   live_result("not_found", None, None, NOW) 的 title/url/description/summary 皆 None
test_write_document_roundtrip: 寫到 tempfile 目錄下不存在的子資料夾，讀回 json 相等，檔案文字含「台灣加權指數」原字（非 \u 跳脫）
```

- [ ] **Step 2–4:** 失敗 → 實作 → 通過
- [ ] **Step 5:** Commit `feat: build and write the data document`

---

### Task 5: 兩階段流程（`run.py`）

**Files:**
- Create: `twopen/run.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: Task 2–4 的所有 Produces
- Produces:
  - 常數 `OPEN_AT = time(9, 0, 30)`、`QUOTE_DEADLINE = time(9, 10)`、`LIVE_DEADLINE = time(10, 0)`、`QUOTE_INTERVAL = 20`、`LIVE_INTERVAL = 60`、`GEMINI_INTERVAL = 120`
  - `@dataclass class Deps: now: Callable[[], datetime]; sleep: Callable[[float], None]; fetch_mis: Callable[[], object]; fetch_official: Callable[[], object]; find_video: Callable[[date], dict | None]; video_finished: Callable[[str], bool]; summarize: Callable[[str], str | None]; publish: Callable[[dict], None]; log: Callable[[str], None]`
  - `run(deps: Deps) -> dict`：回傳最後發佈的文件。永不丟例外。
  - `main() -> int`：組出真實的 `Deps` 並執行；一律回傳 0（資料檔本身帶狀態）。`python -m twopen.run` 可執行。
  - 發佈行為：寫到 `out/latest.json`；若環境變數 `TWOPEN_PUBLISH_DIR` 有設定，另外寫到該目錄的 `latest.json`，並在該目錄執行 `git add latest.json`、`git commit -m "data: <trade_date> <HH:MM>"`、`git push`（內容沒變時略過提交；git 失敗只記錄，不中斷）。

**流程（`run` 的行為，測試據此撰寫）：**
1. `today = deps.now()` 的台灣日期。若現在早於 `OPEN_AT`，睡到 `OPEN_AT`（一次睡眠，秒數為差值）；否則不睡。
2. 查價迴圈：呼叫 `fetch_mis`（`FetchError` 視為空結果並記錄）。`all_open` 為真就跳出；否則若現在 ≥ `QUOTE_DEADLINE` 跳出；否則睡 `QUOTE_INTERVAL` 再查。**至少查一次**，即使啟動時已過截止時間。
3. 若最後結果 `index_is_today` 為假 → 發佈 `market_status="closed"`、`quotes=[]`、`live=pending_live()`，結束（不呼叫任何直播函式）。
4. 呼叫 `fetch_official`（失敗視為 `{}`），`build_quotes`，發佈 `market_status="open"`、`live=pending_live()`。
5. 直播迴圈，直到取得結果或現在 ≥ `LIVE_DEADLINE`：
   - 還沒找到影片 → `find_video(today)`；沒有就睡 `LIVE_INTERVAL` 重來。
   - 找到但 `video_finished` 為假 → 睡 `LIVE_INTERVAL` 重來。
   - 已結束 → `summarize(url)`；有文字 → 結果 `ok`；`None` 或 `FetchError` → 睡 `GEMINI_INTERVAL` 重來。
   - 迴圈內任何 `FetchError` 都記錄後照對應間隔重試。**至少嘗試一次**，即使啟動時已過截止時間。
6. 截止時：有影片無摘要 → `title_only`；無影片 → `not_found`。發佈含最終 `live` 的文件。
7. 步驟 1–6 以外層 `try/except Exception` 包住：發生未預期例外時發佈 `build_document(now, "error", 已有的quotes或[], 已有的live或pending_live(), error="<例外類別名稱>: <訊息>")`。若已經成功發佈過開盤數據，`market_status` 維持 `"open"`，只填 `error`。

- [ ] **Step 1: 寫失敗的測試**

測試用的假時鐘：`FakeClock(start)`，`now()` 回傳目前時間，`sleep(s)` 把時間往前推 `s` 秒並記錄；`published` 清單收集每次發佈的文件深拷貝。提供 `make_deps(clock, **overrides)`，預設來源回傳 Task 2/3 的正常樣本（找到影片、已結束、摘要 `"摘要"`）。

```python
test_happy_path:               08:30 啟動 -> 第一次睡眠 == 1830 秒；發佈 2 次；
                               published[0]: market_status "open"、4 筆 quotes 皆 ok、live.status "pending"
                               published[-1]: live.status "ok"、summary "摘要"、url 以 watch?v= 結尾含影片代號
test_closed_market:            fetch_mis 的 d 全為 "20261113" -> 發佈 1 次、market_status "closed"、find_video 呼叫 0 次；
                               最後一次查價時間 >= 09:10
test_quotes_arrive_late:       前 3 次 fetch_mis 的 0050 "o" 為 "-"，第 4 次正常 -> 4 筆皆 ok，睡眠記錄含三個 20
test_quote_still_missing:      00685L 永遠是 "-" -> 該筆 "missing"、其餘 ok、market_status "open"、仍進入直播階段
test_mis_fetch_error_retries:  前 2 次 fetch_mis 丟 FetchError，第 3 次正常 -> 4 筆皆 ok
test_official_failure_ignored: fetch_official 丟 FetchError -> quotes 皆 ok、official_prev_close 為 None
test_live_waits_for_end:       video_finished 前 5 次 False -> 最終 ok；summarize 只在結束後被呼叫
test_live_not_found:           find_video 永遠 None -> 最終 live.status "not_found"，最後時間 >= 10:00，quotes 不受影響
test_live_title_only:          summarize 永遠 None -> "title_only"，title 與 url 有值、summary None
test_gemini_fetch_error:       summarize 永遠丟 FetchError -> "title_only"，不當機
test_late_start_after_0910:    09:20 啟動 -> 沒有負數睡眠（所有 sleep 參數 > 0）、fetch_mis 至少呼叫 1 次、quotes 皆 ok
test_late_start_after_1000:    10:05 啟動 -> quotes 皆 ok；find_video、summarize 各至少呼叫 1 次；live.status "ok"
test_unexpected_exception:     find_video 丟 ValueError("壞掉")（非 FetchError）->
                               run 不丟例外；最後發佈的文件 trade_date "2026-11-16"、market_status "open"、quotes 4 筆、error 含 "ValueError"
test_exception_before_quotes:  fetch_mis 丟 RuntimeError("x") -> 發佈 1 次、market_status "error"、trade_date 為今天、error 非空
test_publish_failure_does_not_stop: publish 第一次丟 OSError -> run 仍完成並再次嘗試發佈最終文件
```

- [ ] **Step 2:** 跑測試 → 失敗
- [ ] **Step 3:** 實作 `run` 與 `main`。`main` 的 `log` 用 `print(..., flush=True)`，訊息帶台灣時間；缺少金鑰時直播階段直接視為 `not_found`，並在 `error` 註明缺少哪個環境變數。
- [ ] **Step 4:** 全部測試通過：`python -m unittest discover -s tests -v`
- [ ] **Step 5:** Commit `feat: orchestrate quote and livestream stages`

---

### Task 6: 排程、發佈、清理

**Files:**
- Create: `.github/workflows/daily.yml`
- Delete: `spike/probe.py`, `.github/workflows/probe.yml`
- Modify: `.gitignore`（加入 `out/`）

- [ ] **Step 1:** 建立孤立的 `data` 分支，內含一個 `latest.json`，內容為 `{"market_status": "error", "error": "尚未產生資料"}`，推到 origin。之後回到 `main`。
- [ ] **Step 2:** 寫 `daily.yml`：

```yaml
name: daily
on:
  schedule:
    - cron: "30 0 * * 1-5"      # 台灣時間平日 08:30
  workflow_dispatch:
permissions:
  contents: write
concurrency:
  group: daily
  cancel-in-progress: false
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 100
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4
        with: { ref: data, path: data-branch }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python -m unittest discover -s tests
      - run: |
          git -C data-branch config user.name "github-actions[bot]"
          git -C data-branch config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          python -m twopen.run
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}
          TWOPEN_PUBLISH_DIR: data-branch
```

- [ ] **Step 3:** 刪除 `spike/` 與 `probe.yml`，更新 `.gitignore`，Commit `ci: add daily workflow and remove probe`，推送。
- [ ] **Step 4: 驗證** `gh workflow run daily.yml`，等它結束後：
  - `gh run view <id> --log` 中沒有金鑰或帶 `key=` 的網址。
  - `git fetch origin data && git show origin/data:latest.json` 的 `trade_date` 是今天（台灣時間），結構符合設計文件 5.4。
  - 在盤後或假日手動觸發時，預期 `quotes` 仍為 `ok`（即時報價盤後保留當日今開）或 `market_status="closed"`；兩者都算通過，記下實際結果。

---

### Task 7: 新版 Claude 提示詞

**Files:**
- Create: `docs/claude-task-prompt.md`

- [ ] **Step 1:** 以使用者現有提示詞全文為底（保留在對話中，原樣複製），只改下列部分：
  - 開頭加一段：先讀 `https://raw.githubusercontent.com/ccoyzz/tw-stock-open-report/data/latest.json`；以台灣時間判斷今天日期；`trade_date` 不是今天、讀取失敗、或 `market_status` 為 `error` 時，視為資料檔不可用。
  - `market_status` 為 `closed` 時，仍照原第一步用網路搜尋確認休市原因後回報。
  - 第二段（直播）：`live.status` 為 `ok` → 根據 `summary` 用自己的話分析、比較與其他消息來源的異同、附 `url`；`title_only` → 寫「僅取得標題」並附標題與連結；`pending`、`not_found` 或資料檔不可用 → 寫「今日未取得直播內容」。刪除錯誤的頻道代號、RSS、Apple Podcasts、PodSight 步驟。保留「不要憑標題推測或編造內容」。
  - 第三段（開盤數據）：表格數字直接取 `quotes`（`prev_close`、`open`、`change`、`change_pct`），不再自行計算；`status` 為 `missing` 的列寫「今日開盤未取得」；`prev_close_mismatch` 為真時在表格下註記兩個值；來源註明「證交所即時報價（經自動抓取程式）」。資料檔不可用時，退回原提示詞第三段的完整做法（原文保留為備援步驟）。
  - 檔案最上方加一段給使用者的說明：排程時間請設為台灣 09:40，等於雪梨夏令時間 12:40、非夏令時間 11:40。
- [ ] **Step 2:** Commit `docs: add updated Claude task prompt`，推送。

---

### Task 8: 公開儲存庫與上線確認

- [ ] **Step 1:** 確認 `main` 與 `data` 的所有檔案與提交訊息都不含金鑰：`git log -p --all | grep -iE "AIza|api[_-]?key *[:=] *[A-Za-z0-9_-]{20,}"` 應無輸出。
- [ ] **Step 2:** 向使用者再次確認後執行 `gh repo edit ccoyzz/tw-stock-open-report --visibility public --accept-visibility-change-consequences`。
- [ ] **Step 3:** 未登入狀態讀取 raw 網址（`curl -s https://raw.githubusercontent.com/ccoyzz/tw-stock-open-report/data/latest.json`），確認回傳 JSON。
- [ ] **Step 4:** 把 `docs/claude-task-prompt.md` 的內容交給使用者貼回 Claude 排程任務。
- [ ] **Step 5:** 下一個交易日早上檢查：`data` 分支第一次提交時間（預期約 09:01–09:03）、`live.updated_at`（預期 09:15–09:25）與 `live.status`。這是設計文件第 2 節「尚未驗證」的項目，把結果告訴使用者。
