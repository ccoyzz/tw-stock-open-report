import unittest
from datetime import date

from twopen.live import (CHANNEL_ID, GEMINI_MODEL, SUMMARY_PROMPT, UPLOADS_PLAYLIST,
                         extract_summary, is_finished, pick_today_video,
                         title_matches_date)

T16 = "2026/11/16(一)輝達崩跌 散戶狂抄底【早晨財經速解讀】"
T13 = "2026/11/13(五)黃仁勳退位【早晨財經速解讀】"


def playlist(*pairs):
    return {"items": [{"snippet": {"title": t, "description": "說明",
                                   "resourceId": {"videoId": i}}} for i, t in pairs]}


def gemini(*texts):
    return {"candidates": [{"content": {"parts": [{"text": t} for t in texts]}}]}


class ConstantsTest(unittest.TestCase):
    def test_channel_constants(self):
        self.assertEqual(CHANNEL_ID, "UC0lbAQVpenvfA2QqzsRtL_g")
        self.assertEqual(UPLOADS_PLAYLIST, "UU0lbAQVpenvfA2QqzsRtL_g")
        self.assertEqual(GEMINI_MODEL, "gemini-flash-latest")

    def test_prompt_forbids_guessing(self):
        self.assertIn("繁體中文", SUMMARY_PROMPT)
        self.assertIn("不要推測", SUMMARY_PROMPT)


class TitleTest(unittest.TestCase):
    def test_title_matches_exact_day(self):
        self.assertTrue(title_matches_date(T16, date(2026, 11, 16)))
        self.assertFalse(title_matches_date(T16, date(2026, 11, 13)))

    def test_single_digit_day_not_prefix(self):
        self.assertFalse(title_matches_date(T16, date(2026, 11, 1)))

    def test_zero_padded_and_unpadded(self):
        self.assertTrue(title_matches_date("2026/11/06(五)標題", date(2026, 11, 6)))
        self.assertTrue(title_matches_date("2026/11/6(五)標題", date(2026, 11, 6)))
        self.assertTrue(title_matches_date("2026/1/5(一)標題", date(2026, 1, 5)))
        self.assertTrue(title_matches_date("2026/01/05(一)標題", date(2026, 1, 5)))

    def test_month_not_suffix_of_longer_month(self):
        self.assertFalse(title_matches_date("2026/11/5(四)標題", date(2026, 1, 5)))

    def test_title_with_leading_text(self):
        self.assertTrue(title_matches_date("【直播】" + T16, date(2026, 11, 16)))

    def test_non_string_title(self):
        self.assertFalse(title_matches_date(None, date(2026, 11, 16)))


class PickVideoTest(unittest.TestCase):
    def test_pick_today_video(self):
        self.assertEqual(
            pick_today_video(playlist(("a", T16), ("b", T13)), date(2026, 11, 16)),
            {"id": "a", "title": T16, "description": "說明",
             "url": "https://www.youtube.com/watch?v=a"})

    def test_pick_returns_none(self):
        self.assertIsNone(pick_today_video(playlist(("b", T13)), date(2026, 11, 16)))
        for bad in ({}, None, {"items": [{}]}, {"items": None}, {"items": [None]},
                    {"items": [{"snippet": {"title": T16}}]}):
            self.assertIsNone(pick_today_video(bad, date(2026, 11, 16)))


class FinishedTest(unittest.TestCase):
    def test_is_finished(self):
        self.assertTrue(is_finished({"items": [{"snippet": {"liveBroadcastContent": "none"}}]}))
        self.assertFalse(is_finished({"items": [
            {"snippet": {"liveBroadcastContent": "live"}, "liveStreamingDetails": {}}]}))
        self.assertTrue(is_finished({"items": [
            {"snippet": {"liveBroadcastContent": "live"},
             "liveStreamingDetails": {"actualEndTime": "2026-11-16T01:05:00Z"}}]}))
        self.assertFalse(is_finished({"items": [{"snippet": {"liveBroadcastContent": "upcoming"}}]}))
        self.assertFalse(is_finished({}))
        self.assertFalse(is_finished({"items": []}))
        self.assertFalse(is_finished(None))


class ExtractSummaryTest(unittest.TestCase):
    def test_extract_summary(self):
        self.assertEqual(extract_summary(gemini("重點一", "重點二")), "重點一重點二")

    def test_extract_summary_empty_cases(self):
        for bad in ({}, None, {"candidates": []}, {"candidates": [{"finishReason": "SAFETY"}]},
                    gemini("  ", "\n"), {"candidates": [{"content": {"parts": [{}]}}]}):
            self.assertIsNone(extract_summary(bad))

    def test_unreadable_marker_is_none(self):
        self.assertIsNone(extract_summary(gemini("無法讀取影片內容")))


if __name__ == "__main__":
    unittest.main()
