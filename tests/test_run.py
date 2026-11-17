import copy
import unittest
from datetime import datetime, timedelta

from twopen.http import FetchError
from twopen.report import TPE
from twopen.run import Deps, already_complete, run

from .test_quotes import MIS, OFFICIAL, mis_with

VIDEO = {"id": "vid16", "title": "2026/11/16(一)標題", "description": "說明",
         "url": "https://www.youtube.com/watch?v=vid16"}


def at(hour, minute, second=0):
    return datetime(2026, 11, 16, hour, minute, second, tzinfo=TPE)


class FakeClock:
    def __init__(self, start):
        self.current = start
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class Seq:
    """依序回傳值；值是例外就丟出；用完後重複最後一個。記錄每次呼叫的時間與參數。"""

    def __init__(self, clock, *values):
        self.clock, self.values, self.calls = clock, list(values), []

    def __call__(self, *args):
        self.calls.append((self.clock.now(), args))
        value = self.values[min(len(self.calls), len(self.values)) - 1]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


class Harness:
    def __init__(self, start, **overrides):
        self.clock = FakeClock(start)
        self.published = []
        self.logs = []
        sources = {"fetch_mis": [MIS], "fetch_official": [OFFICIAL], "find_video": [VIDEO],
                   "video_finished": [True], "summarize": ["摘要"]}
        skip = overrides.pop("skip_live_reason", None)
        publish = overrides.pop("publish", None)
        for name, values in overrides.items():
            sources[name] = values
        self.seq = {name: Seq(self.clock, *values) for name, values in sources.items()}
        self.deps = Deps(now=self.clock.now, sleep=self.clock.sleep,
                         publish=publish or self._publish, log=self.logs.append,
                         skip_live_reason=skip, **self.seq)

    def _publish(self, doc):
        self.published.append(copy.deepcopy(doc))

    def run(self):
        return run(self.deps)

    def calls(self, name):
        return len(self.seq[name].calls)


def statuses(doc):
    return {q["code"]: q["status"] for q in doc["quotes"]}


ALL_OK = {"TAIEX": "ok", "0050": "ok", "00631L": "ok", "00685L": "ok"}


class QuoteStageTest(unittest.TestCase):
    def test_happy_path(self):
        h = Harness(at(8, 30))
        result = h.run()
        self.assertEqual(h.clock.sleeps[0], 1830)
        self.assertEqual(len(h.published), 2)
        first, last = h.published
        self.assertEqual(first["market_status"], "open")
        self.assertEqual(statuses(first), ALL_OK)
        self.assertEqual(first["live"]["status"], "pending")
        self.assertEqual(first["trade_date"], "2026-11-16")
        self.assertEqual(last["live"]["status"], "ok")
        self.assertEqual(last["live"]["summary"], "摘要")
        self.assertEqual(last["live"]["url"], "https://www.youtube.com/watch?v=vid16")
        self.assertEqual(statuses(last), ALL_OK)
        self.assertIsNone(last["error"])
        self.assertEqual(result, last)
        self.assertEqual(h.seq["summarize"].calls[0][1], (VIDEO["url"],))

    def test_first_quote_query_is_not_before_open(self):
        h = Harness(at(8, 30))
        h.run()
        self.assertGreaterEqual(h.seq["fetch_mis"].calls[0][0], at(9, 0, 30))

    def test_closed_market(self):
        stale = copy.deepcopy(MIS)
        for row in stale["msgArray"]:
            row["d"] = "20261113"
        h = Harness(at(8, 30), fetch_mis=[stale])
        h.run()
        self.assertEqual(len(h.published), 1)
        self.assertEqual(h.published[0]["market_status"], "closed")
        self.assertEqual(h.published[0]["quotes"], [])
        self.assertEqual(h.calls("find_video"), 0)
        self.assertGreaterEqual(h.seq["fetch_mis"].calls[-1][0], at(9, 10))

    def test_quotes_arrive_late(self):
        late = mis_with("0050", o="-")
        h = Harness(at(9, 0, 30), fetch_mis=[late, late, late, MIS])
        h.run()
        self.assertEqual(statuses(h.published[0]), ALL_OK)
        self.assertEqual(h.clock.sleeps[:3], [20, 20, 20])
        self.assertEqual(h.calls("fetch_mis"), 4)

    def test_quote_still_missing(self):
        h = Harness(at(9, 0, 30), fetch_mis=[mis_with("00685L", o="-")])
        h.run()
        first = h.published[0]
        self.assertEqual(first["market_status"], "open")
        self.assertEqual(statuses(first), {**ALL_OK, "00685L": "missing"})
        self.assertEqual(h.published[-1]["live"]["status"], "ok")

    def test_mis_fetch_error_retries(self):
        err = FetchError("證交所即時報價: HTTP 503")
        h = Harness(at(9, 0, 30), fetch_mis=[err, err, MIS])
        h.run()
        self.assertEqual(statuses(h.published[0]), ALL_OK)

    def test_mis_always_failing_is_error_not_closed(self):
        h = Harness(at(9, 0, 30), fetch_mis=[FetchError("證交所即時報價: HTTP 503")])
        h.run()
        last = h.published[-1]
        self.assertEqual(last["market_status"], "error")
        self.assertIn("證交所即時報價", last["error"])
        self.assertEqual(last["trade_date"], "2026-11-16")

    def test_official_failure_ignored(self):
        h = Harness(at(9, 0, 30), fetch_official=[FetchError("證交所OpenAPI: HTTP 500")])
        h.run()
        first = h.published[0]
        self.assertEqual(statuses(first), ALL_OK)
        self.assertTrue(all(q["official_prev_close"] is None for q in first["quotes"]))

    def test_late_start_after_0910(self):
        h = Harness(at(9, 20))
        h.run()
        self.assertTrue(all(s > 0 for s in h.clock.sleeps))
        self.assertGreaterEqual(h.calls("fetch_mis"), 1)
        self.assertEqual(statuses(h.published[0]), ALL_OK)


class LiveStageTest(unittest.TestCase):
    def test_live_waits_for_end(self):
        h = Harness(at(9, 0, 30), video_finished=[False] * 5 + [True])
        h.run()
        self.assertEqual(h.published[-1]["live"]["status"], "ok")
        self.assertEqual(h.calls("summarize"), 1)
        self.assertGreater(h.seq["summarize"].calls[0][0], h.seq["video_finished"].calls[4][0])
        self.assertEqual(h.clock.sleeps.count(60), 5)

    def test_video_appears_later(self):
        h = Harness(at(9, 0, 30), find_video=[None, None, VIDEO])
        h.run()
        self.assertEqual(h.published[-1]["live"]["status"], "ok")
        self.assertEqual(h.calls("find_video"), 3)

    def test_live_not_found(self):
        h = Harness(at(9, 0, 30), find_video=[None])
        h.run()
        last = h.published[-1]
        self.assertEqual(last["live"]["status"], "not_found")
        self.assertIsNone(last["live"]["url"])
        self.assertGreaterEqual(h.clock.now(), at(10, 0))
        self.assertLess(h.clock.now(), at(10, 2))
        self.assertEqual(statuses(last), ALL_OK)
        self.assertEqual(last["market_status"], "open")

    def test_live_title_only(self):
        h = Harness(at(9, 0, 30), summarize=[None])
        h.run()
        live = h.published[-1]["live"]
        self.assertEqual(live["status"], "title_only")
        self.assertEqual(live["title"], VIDEO["title"])
        self.assertEqual(live["url"], VIDEO["url"])
        self.assertIsNone(live["summary"])
        self.assertIn(120, h.clock.sleeps)

    def test_gemini_fetch_error(self):
        h = Harness(at(9, 0, 30), summarize=[FetchError("Gemini摘要: HTTP 500")])
        h.run()
        self.assertEqual(h.published[-1]["live"]["status"], "title_only")

    def test_gemini_recovers_after_errors(self):
        h = Harness(at(9, 0, 30), summarize=[FetchError("Gemini摘要: HTTP 500"), None, "摘要"])
        h.run()
        self.assertEqual(h.published[-1]["live"]["status"], "ok")

    def test_youtube_fetch_error_retries(self):
        h = Harness(at(9, 0, 30), find_video=[FetchError("YouTube影片清單: HTTP 500"), VIDEO])
        h.run()
        self.assertEqual(h.published[-1]["live"]["status"], "ok")

    def test_late_start_after_1000(self):
        h = Harness(at(10, 5))
        h.run()
        last = h.published[-1]
        self.assertEqual(statuses(last), ALL_OK)
        self.assertGreaterEqual(h.calls("find_video"), 1)
        self.assertGreaterEqual(h.calls("summarize"), 1)
        self.assertEqual(last["live"]["status"], "ok")
        self.assertTrue(all(s > 0 for s in h.clock.sleeps))

    def test_skip_live_reason(self):
        h = Harness(at(9, 0, 30), skip_live_reason="缺少環境變數 YOUTUBE_API_KEY")
        h.run()
        last = h.published[-1]
        self.assertEqual(last["live"]["status"], "not_found")
        self.assertIn("YOUTUBE_API_KEY", last["error"])
        self.assertEqual(h.calls("find_video"), 0)
        self.assertEqual(statuses(last), ALL_OK)


class FailureTest(unittest.TestCase):
    def test_unexpected_exception(self):
        h = Harness(at(9, 0, 30), find_video=[ValueError("壞掉")])
        h.run()
        last = h.published[-1]
        self.assertEqual(last["trade_date"], "2026-11-16")
        self.assertEqual(last["market_status"], "open")
        self.assertEqual(len(last["quotes"]), 4)
        self.assertIn("ValueError", last["error"])

    def test_exception_before_quotes(self):
        h = Harness(at(9, 0, 30), fetch_mis=[RuntimeError("x")])
        h.run()
        self.assertEqual(len(h.published), 1)
        self.assertEqual(h.published[0]["market_status"], "error")
        self.assertEqual(h.published[0]["trade_date"], "2026-11-16")
        self.assertTrue(h.published[0]["error"])

    def test_error_text_hides_urls(self):
        h = Harness(at(9, 0, 30),
                    fetch_mis=[RuntimeError("boom https://x.test/a?key=SECRETKEY end")])
        h.run()
        error = h.published[0]["error"]
        self.assertNotIn("SECRETKEY", error)
        self.assertNotIn("x.test", error)
        self.assertFalse(any("SECRETKEY" in line for line in h.logs))

    def test_publish_failure_does_not_stop(self):
        attempts = []

        def publish(doc):
            attempts.append(copy.deepcopy(doc))
            if len(attempts) == 1:
                raise OSError("disk")

        h = Harness(at(9, 0, 30), publish=publish)
        result = h.run()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[-1]["live"]["status"], "ok")
        self.assertEqual(result["live"]["status"], "ok")


class AlreadyCompleteTest(unittest.TestCase):
    TODAY = at(9, 30).date()

    def doc(self, **changes):
        base = {"trade_date": "2026-11-16", "market_status": "open", "error": None,
                "quotes": [{"status": "ok"}] * 4, "live": {"status": "ok"}}
        base.update(changes)
        return base

    def test_complete_open_day(self):
        self.assertTrue(already_complete(self.doc(), self.TODAY))

    def test_closed_day_is_complete(self):
        self.assertTrue(already_complete(self.doc(market_status="closed", quotes=[],
                                                  live={"status": "pending"}), self.TODAY))

    def test_other_day_is_not_complete(self):
        self.assertFalse(already_complete(self.doc(trade_date="2026-11-13"), self.TODAY))

    def test_unfinished_documents_are_not_complete(self):
        for changes in ({"live": {"status": "pending"}}, {"live": {"status": "title_only"}},
                        {"live": {"status": "not_found"}}, {"market_status": "error"},
                        {"quotes": [{"status": "ok"}] * 3 + [{"status": "missing"}]},
                        {"quotes": []}):
            self.assertFalse(already_complete(self.doc(**changes), self.TODAY), changes)

    def test_malformed_documents_are_not_complete(self):
        for bad in (None, {}, [], "x", {"trade_date": "2026-11-16"},
                    {"trade_date": "2026-11-16", "market_status": "open", "quotes": None,
                     "live": None}):
            self.assertFalse(already_complete(bad, self.TODAY))


if __name__ == "__main__":
    unittest.main()
