"""HTTP 請求。錯誤訊息只含來源名稱與原因類別，絕不包含網址（網址可能帶金鑰）。"""
import json
import urllib.error
import urllib.request

USER_AGENT = "tw-stock-open-report/1.0"


class FetchError(Exception):
    """外部來源讀取失敗。訊息格式：「<label>: <原因>」。"""


def get_json(url, *, label, headers=None, data=None, timeout=30,
             opener=urllib.request.urlopen):
    request_headers = {"User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=request_headers)
    try:
        with opener(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"{label}: HTTP {e.code}") from None
    except Exception as e:  # URLError、逾時、連線中斷等；不可用 str(e)，可能含網址
        raise FetchError(f"{label}: {type(e).__name__}") from None
    try:
        return json.loads(body)
    except ValueError:
        raise FetchError(f"{label}: 回應不是 JSON") from None
