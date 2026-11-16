import io
import unittest
import urllib.error

from twopen.http import FetchError, get_json

SECRET_URL = "https://example.test/x?key=SECRETKEY"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_opener(payload=b'{"a": 1}', exc=None, seen=None):
    def opener(req, timeout=None):
        if seen is not None:
            seen.append(req)
        if exc:
            raise exc
        return _Resp(payload)
    return opener


class GetJsonTest(unittest.TestCase):
    def assert_no_leak(self, exc):
        self.assertNotIn("SECRETKEY", str(exc))
        self.assertNotIn("example.test", str(exc))

    def test_returns_parsed_json(self):
        self.assertEqual(get_json(SECRET_URL, label="測試", opener=fake_opener()), {"a": 1})

    def test_sends_user_agent_and_headers(self):
        seen = []
        get_json(SECRET_URL, label="測試", headers={"X-Test": "1"}, opener=fake_opener(seen=seen))
        self.assertEqual(seen[0].get_header("User-agent"), "tw-stock-open-report/1.0")
        self.assertEqual(seen[0].get_header("X-test"), "1")

    def test_http_error_message_has_label_and_code_but_no_url(self):
        err = urllib.error.HTTPError(SECRET_URL, 403, "Forbidden", {}, io.BytesIO(b""))
        with self.assertRaises(FetchError) as cm:
            get_json(SECRET_URL, label="即時報價", opener=fake_opener(exc=err))
        self.assertIn("即時報價", str(cm.exception))
        self.assertIn("403", str(cm.exception))
        self.assert_no_leak(cm.exception)

    def test_url_error_becomes_fetch_error_without_url(self):
        with self.assertRaises(FetchError) as cm:
            get_json(SECRET_URL, label="測試", opener=fake_opener(exc=urllib.error.URLError(SECRET_URL)))
        self.assert_no_leak(cm.exception)

    def test_timeout_becomes_fetch_error_without_url(self):
        with self.assertRaises(FetchError) as cm:
            get_json(SECRET_URL, label="測試", opener=fake_opener(exc=TimeoutError(SECRET_URL)))
        self.assert_no_leak(cm.exception)

    def test_invalid_json_becomes_fetch_error(self):
        with self.assertRaises(FetchError) as cm:
            get_json(SECRET_URL, label="測試", opener=fake_opener(payload=b"<html>"))
        self.assertIn("測試", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
