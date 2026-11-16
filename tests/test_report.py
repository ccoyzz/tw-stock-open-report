import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from twopen.report import (TPE, build_document, live_result, pending_live,
                           write_document)

NOW = datetime(2026, 11, 16, 9, 1, 12, 345678, tzinfo=TPE)
VIDEO = {"id": "a", "title": "T", "description": "D", "url": "U"}


class ReportTest(unittest.TestCase):
    def test_build_document_fields(self):
        doc = build_document(NOW, "open", [], pending_live())
        self.assertEqual(doc["generated_at"], "2026-11-16T09:01:12+08:00")
        self.assertEqual(doc["trade_date"], "2026-11-16")
        self.assertEqual(doc["market_status"], "open")
        self.assertEqual(doc["quotes"], [])
        self.assertEqual(doc["live"]["status"], "pending")
        self.assertIsNone(doc["error"])

    def test_trade_date_uses_taipei(self):
        doc = build_document(datetime(2026, 11, 16, 0, 30, tzinfo=timezone.utc), "open", [],
                             pending_live())
        self.assertEqual(doc["trade_date"], "2026-11-16")
        self.assertEqual(doc["generated_at"], "2026-11-16T08:30:00+08:00")
        doc = build_document(datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc), "open", [],
                             pending_live())
        self.assertEqual(doc["trade_date"], "2026-11-16")
        self.assertTrue(doc["generated_at"].endswith("+08:00"))

    def test_error_is_recorded(self):
        doc = build_document(NOW, "error", [], pending_live(), error="ValueError: 壞掉")
        self.assertEqual(doc["error"], "ValueError: 壞掉")

    def test_pending_live(self):
        self.assertEqual(pending_live(), {"status": "pending", "title": None, "url": None,
                                          "description": None, "summary": None,
                                          "updated_at": None})

    def test_live_result_ok(self):
        self.assertEqual(live_result("ok", VIDEO, "S", NOW), {
            "status": "ok", "title": "T", "url": "U", "description": "D", "summary": "S",
            "updated_at": "2026-11-16T09:01:12+08:00"})

    def test_live_result_not_found(self):
        result = live_result("not_found", None, None, NOW)
        self.assertEqual(result["status"], "not_found")
        for key in ("title", "url", "description", "summary"):
            self.assertIsNone(result[key])

    def test_write_document_roundtrip(self):
        doc = build_document(NOW, "open", [{"name": "台灣加權指數"}], pending_live())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "dir" / "latest.json"
            write_document(doc, path)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text), doc)
        self.assertIn("台灣加權指數", text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
