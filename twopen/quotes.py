"""開盤數據：證交所即時報價與官方前日收盤的抓取、解析、漲跌計算。"""
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .http import get_json

# (名稱, 代號, 即時報價頻道)
SYMBOLS = [
    ("台灣加權指數", "TAIEX", "tse_t00.tw"),
    ("元大台灣50", "0050", "tse_0050.tw"),
    ("元大台灣50正2", "00631L", "tse_00631L.tw"),
    ("群益臺灣加權正2", "00685L", "tse_00685L.tw"),
]
_MIS_CODE = {"t00": "TAIEX", "0050": "0050", "00631L": "00631L", "00685L": "00685L"}
_ETF_CODES = {"0050", "00631L", "00685L"}
SOURCE = "證交所即時報價"
_CENT = Decimal("0.01")

MIS_URL = ("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch="
           + urllib.parse.quote("|".join(s[2] for s in SYMBOLS)))
OFFICIAL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"


@dataclass(frozen=True)
class MisRow:
    code: str
    day: date | None
    open: Decimal | None
    prev_close: Decimal | None


def _price(value):
    """轉成正的 Decimal；「-」、空值、非數字、零或負數都回傳 None。"""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _day(value):
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError:
        return None


def parse_mis(payload):
    rows = {}
    items = payload.get("msgArray") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code = _MIS_CODE.get(item.get("c"))
        if code:
            rows[code] = MisRow(code, _day(item.get("d")), _price(item.get("o")),
                                _price(item.get("y")))
    return rows


def index_is_today(rows, today):
    row = rows.get("TAIEX")
    return row is not None and row.day == today


def all_open(rows, today):
    return all(
        (row := rows.get(code)) is not None and row.day == today and row.open is not None
        for _, code, _ in SYMBOLS)


def parse_official(rows):
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("Code") in _ETF_CODES:
            price = _price(row.get("ClosingPrice"))
            if price is not None:
                result[row["Code"]] = price
    return result


def _fmt(number):
    return str(number.quantize(_CENT, rounding=ROUND_HALF_UP))


def _signed(number):
    text = _fmt(number)
    return "+" + text if Decimal(text) > 0 else text


def compute_change(open_, prev_close):
    change = open_ - prev_close
    return _signed(change), _signed(change / prev_close * 100)


def build_quotes(rows, official, today):
    quotes = []
    for name, code, _ in SYMBOLS:
        row = rows.get(code)
        quote = {"name": name, "code": code, "status": "missing", "reason": None,
                 "prev_close": None, "open": None, "change": None, "change_pct": None,
                 "official_prev_close": _fmt(official[code]) if code in official else None,
                 "prev_close_mismatch": False, "source": SOURCE}
        if row is None:
            quote["reason"] = "即時報價沒有回傳這個標的"
        elif row.day != today:
            quote["reason"] = "即時報價的資料日期不是今天"
        else:
            if row.prev_close is not None:
                quote["prev_close"] = _fmt(row.prev_close)
                if code in official:
                    quote["prev_close_mismatch"] = _fmt(official[code]) != quote["prev_close"]
            if row.open is None:
                quote["reason"] = "尚無今日開盤價"
            elif row.prev_close is None:
                quote["open"] = _fmt(row.open)
                quote["reason"] = "即時報價沒有昨收，無法計算漲跌"
            else:
                quote["open"] = _fmt(row.open)
                quote["change"], quote["change_pct"] = compute_change(row.open, row.prev_close)
                quote["status"] = "ok"
        quotes.append(quote)
    return quotes


def fetch_mis():
    return get_json(MIS_URL, label="證交所即時報價")


def fetch_official():
    return get_json(OFFICIAL_URL, label="證交所OpenAPI")
