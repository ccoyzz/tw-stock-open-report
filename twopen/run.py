"""進入點：階段一抓開盤數據，階段二等直播結束後產生摘要。所有時間都是台灣時間。"""
import json
import os
import re
import subprocess
import sys
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable

from . import live, quotes
from .http import FetchError
from .report import TPE, build_document, live_result, pending_live, write_document

OPEN_AT = time(9, 0, 30)
QUOTE_DEADLINE = time(9, 10)
LIVE_DEADLINE = time(10, 0)
QUOTE_INTERVAL = 20
LIVE_INTERVAL = 60
GEMINI_INTERVAL = 120


@dataclass
class Deps:
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    fetch_mis: Callable[[], object]
    fetch_official: Callable[[], object]
    find_video: Callable[[date], dict | None]
    video_finished: Callable[[str], bool]
    summarize: Callable[[str], str | None]
    publish: Callable[[dict], None]
    log: Callable[[str], None]
    skip_live_reason: str | None = None  # 有值就不做直播階段（例如缺少金鑰）


def _clean(text):
    """錯誤文字不得帶網址（可能含金鑰）。"""
    return re.sub(r"https?://\S+", "[網址已隱藏]", str(text))


class _Run:
    def __init__(self, deps):
        self.deps = deps
        self.today = deps.now().astimezone(TPE).date()
        self.quotes = None
        self.live = None
        self.doc = None

    def _now(self):
        return self.deps.now().astimezone(TPE)

    def _at(self, clock_time):
        return datetime.combine(self.today, clock_time, tzinfo=TPE)

    def _publish(self, market_status, error=None):
        self.doc = build_document(self._now(), market_status, self.quotes or [],
                                  self.live or pending_live(), error=error)
        try:
            self.deps.publish(self.doc)
        except Exception as e:  # 發佈失敗不可中斷流程
            self.deps.log(_clean(f"發佈失敗 {type(e).__name__}: {e}"))

    def execute(self):
        wait = (self._at(OPEN_AT) - self._now()).total_seconds()
        if wait > 0:
            self.deps.log(f"等待開盤，{wait:.0f} 秒後開始查價")
            self.deps.sleep(wait)

        rows, last_error = self._poll_quotes()
        if rows is None:
            self._publish("error", error=last_error)
            return
        if not quotes.index_is_today(rows, self.today):
            self.deps.log("加權指數的資料日期不是今天，視為休市")
            self._publish("closed")
            return

        try:
            official = quotes.parse_official(self.deps.fetch_official())
        except FetchError as e:
            self.deps.log(str(e))
            official = {}
        self.quotes = quotes.build_quotes(rows, official, self.today)
        self._publish("open")

        if self.deps.skip_live_reason:
            self.live = live_result("not_found", None, None, self._now())
            self._publish("open", error=self.deps.skip_live_reason)
            return
        self.live = self._wait_for_live()
        self._publish("open")

    def _poll_quotes(self):
        """回傳 (最後一次成功解析的結果或 None, 最後一次錯誤訊息)。至少查一次。"""
        rows, last_error = None, None
        while True:
            try:
                rows = quotes.parse_mis(self.deps.fetch_mis())
                if quotes.all_open(rows, self.today):
                    break
            except FetchError as e:
                last_error = str(e)
                self.deps.log(last_error)
            if self._now() >= self._at(QUOTE_DEADLINE):
                break
            self.deps.sleep(QUOTE_INTERVAL)
        return rows, last_error

    def _wait_for_live(self):
        """至少嘗試一次；截止時回傳 title_only 或 not_found。"""
        video, finished = None, False
        while True:
            interval = LIVE_INTERVAL
            try:
                if video is None:
                    video = self.deps.find_video(self.today)
                if video is not None and not finished:
                    finished = self.deps.video_finished(video["id"])
                if video is not None and finished:
                    interval = GEMINI_INTERVAL
                    summary = self.deps.summarize(video["url"])
                    if summary:
                        return live_result("ok", video, summary, self._now())
                    self.deps.log("Gemini 沒有回傳可用的摘要")
            except FetchError as e:
                self.deps.log(str(e))
            if self._now() >= self._at(LIVE_DEADLINE):
                break
            self.deps.sleep(interval)
        status = "title_only" if video is not None else "not_found"
        return live_result(status, video, None, self._now())


def run(deps):
    """執行兩個階段並回傳最後發佈的文件。任何情況都會發佈帶今天日期的文件，不丟例外。"""
    state = _Run(deps)
    try:
        state.execute()
    except Exception as e:
        error = _clean(f"{type(e).__name__}: {e}")
        deps.log(f"未預期的錯誤 {error}")
        state._publish("open" if state.quotes is not None else "error", error=error)
    return state.doc


def already_complete(doc, today):
    """今天的資料是否已完整發佈過（重複啟動的執行據此直接結束）。"""
    if not isinstance(doc, dict) or doc.get("trade_date") != today.isoformat():
        return False
    if doc.get("market_status") == "closed":
        return True
    quote_list, live_doc = doc.get("quotes"), doc.get("live")
    return (doc.get("market_status") == "open"
            and isinstance(quote_list, list) and len(quote_list) == len(quotes.SYMBOLS)
            and all(isinstance(q, dict) and q.get("status") == "ok" for q in quote_list)
            and isinstance(live_doc, dict) and live_doc.get("status") == "ok")


def _published_document():
    publish_dir = os.environ.get("TWOPEN_PUBLISH_DIR")
    if not publish_dir:
        return None
    try:
        return json.loads((Path(publish_dir) / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True)


def _make_publish(log):
    publish_dir = os.environ.get("TWOPEN_PUBLISH_DIR")

    def publish(doc):
        write_document(doc, Path("out") / "latest.json")
        if not publish_dir:
            return
        write_document(doc, Path(publish_dir) / "latest.json")
        _git(publish_dir, "add", "latest.json")
        if _git(publish_dir, "diff", "--cached", "--quiet").returncode == 0:
            return
        message = f"data: {doc['trade_date']} {doc['generated_at'][11:16]}"
        for step in (("commit", "-q", "-m", message), ("push", "-q")):
            result = _git(publish_dir, *step)
            if result.returncode != 0:
                log(_clean(f"git {step[0]} 失敗: {result.stderr.strip()[:200]}"))
                return
        log(f"已發佈 {message}")

    return publish


def main():
    def log(message):
        print(f"[{datetime.now(TPE):%H:%M:%S}] {message}", flush=True)

    if already_complete(_published_document(), datetime.now(TPE).date()):
        log("今天的資料已完整發佈過，這次執行直接結束")
        return 0

    youtube_key = os.environ.get("YOUTUBE_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    missing = [name for name, value in (("YOUTUBE_API_KEY", youtube_key),
                                        ("GEMINI_API_KEY", gemini_key)) if not value]
    deps = Deps(
        now=lambda: datetime.now(TPE),
        sleep=_time.sleep,
        fetch_mis=quotes.fetch_mis,
        fetch_official=quotes.fetch_official,
        find_video=lambda day: live.find_today_video(day, youtube_key),
        video_finished=lambda video_id: live.video_finished(video_id, youtube_key),
        summarize=lambda url: live.summarize(url, gemini_key),
        publish=_make_publish(log),
        log=log,
        skip_live_reason=("缺少環境變數 " + "、".join(missing)) if missing else None,
    )
    doc = run(deps)
    log(f"完成：market_status={doc['market_status']} "
        f"quotes={[q['status'] for q in doc['quotes']]} live={doc['live']['status']} "
        f"error={doc['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
