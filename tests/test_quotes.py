import copy
import unittest
from datetime import date
from decimal import Decimal

from twopen.quotes import (SYMBOLS, all_open, build_quotes, compute_change,
                           index_is_today, parse_mis, parse_official)

MIS = {"msgArray": [
    {"c": "t00", "d": "20261116", "o": "43512.42", "y": "43675.12", "z": "42288.39"},
    {"c": "0050", "d": "20261116", "o": "101.2000", "y": "102.5000", "z": "-"},
    {"c": "00631L", "d": "20261116", "o": "31.2500", "y": "31.9200", "z": "-"},
    {"c": "00685L", "d": "20261116", "o": "10.1400", "y": "10.4000", "z": "-"}]}
OFFICIAL = [{"Code": "0050", "ClosingPrice": "102.50"},
            {"Code": "00631L", "ClosingPrice": "31.92"},
            {"Code": "00685L", "ClosingPrice": "10.40"},
            {"Code": "2330", "ClosingPrice": "2350.00"}]
TODAY = date(2026, 11, 16)


def mis_with(code, **changes):
    payload = copy.deepcopy(MIS)
    for row in payload["msgArray"]:
        if row["c"] == code:
            row.update(changes)
    return payload


def by_code(quotes):
    return {q["code"]: q for q in quotes}


class ParseMisTest(unittest.TestCase):
    def test_parse_mis_maps_index_and_etfs(self):
        rows = parse_mis(MIS)
        self.assertEqual(rows["TAIEX"].open, Decimal("43512.42"))
        self.assertEqual(rows["0050"].day, TODAY)
        self.assertEqual(len(rows), 4)

    def test_malformed_payloads_do_not_raise(self):
        for payload in ({}, {"msgArray": []}, None, {"msgArray": None}, [],
                        {"msgArray": [None, "x"]},
                        {"msgArray": [{"c": "0050", "o": "abc"}]}):
            parse_mis(payload)
        quotes = build_quotes({}, {}, TODAY)
        self.assertEqual([q["status"] for q in quotes], ["missing"] * 4)

    def test_non_numeric_open_is_none(self):
        rows = parse_mis({"msgArray": [{"c": "0050", "d": "20261116", "o": "abc", "y": "102.5"}]})
        self.assertIsNone(rows["0050"].open)

    def test_zero_or_negative_price_is_none(self):
        rows = parse_mis(mis_with("0050", o="0.0000", y="-1"))
        self.assertIsNone(rows["0050"].open)
        self.assertIsNone(rows["0050"].prev_close)

    def test_index_is_today(self):
        self.assertTrue(index_is_today(parse_mis(MIS), TODAY))
        self.assertFalse(index_is_today(parse_mis(mis_with("t00", d="20261113")), TODAY))
        self.assertFalse(index_is_today({}, TODAY))

    def test_all_open(self):
        self.assertTrue(all_open(parse_mis(MIS), TODAY))
        self.assertFalse(all_open(parse_mis(mis_with("0050", o="-")), TODAY))
        self.assertFalse(all_open(parse_mis({"msgArray": MIS["msgArray"][:3]}), TODAY))


class ComputeChangeTest(unittest.TestCase):
    def test_compute_change_matches_spec(self):
        self.assertEqual(compute_change(Decimal("43512.42"), Decimal("43675.12")), ("-162.70", "-0.37"))

    def test_compute_change_etf(self):
        self.assertEqual(compute_change(Decimal("10.14"), Decimal("10.40")), ("-0.26", "-2.50"))

    def test_compute_change_positive_and_zero(self):
        self.assertEqual(compute_change(Decimal("103"), Decimal("100")), ("+3.00", "+3.00"))
        self.assertEqual(compute_change(Decimal("100"), Decimal("100")), ("0.00", "0.00"))


class BuildQuotesTest(unittest.TestCase):
    def test_build_quotes_ok(self):
        quotes = build_quotes(parse_mis(MIS), parse_official(OFFICIAL), TODAY)
        self.assertEqual([q["code"] for q in quotes], [s[1] for s in SYMBOLS])
        self.assertEqual([q["status"] for q in quotes], ["ok"] * 4)
        q = by_code(quotes)
        self.assertEqual(q["TAIEX"]["name"], "台灣加權指數")
        self.assertEqual(q["TAIEX"]["prev_close"], "43675.12")
        self.assertEqual(q["TAIEX"]["open"], "43512.42")
        self.assertEqual(q["TAIEX"]["change"], "-162.70")
        self.assertEqual(q["TAIEX"]["change_pct"], "-0.37")
        self.assertIsNone(q["TAIEX"]["official_prev_close"])
        self.assertEqual(q["0050"]["open"], "101.20")
        self.assertEqual(q["0050"]["official_prev_close"], "102.50")
        self.assertFalse(q["0050"]["prev_close_mismatch"])
        self.assertEqual(q["0050"]["source"], "證交所即時報價")
        self.assertIsNone(q["0050"]["reason"])

    def test_build_quotes_flags_mismatch(self):
        official = parse_official(OFFICIAL)
        official["0050"] = Decimal("100.95")
        q = by_code(build_quotes(parse_mis(MIS), official, TODAY))["0050"]
        self.assertTrue(q["prev_close_mismatch"])
        self.assertEqual(q["prev_close"], "102.50")
        self.assertEqual(q["official_prev_close"], "100.95")

    def test_dash_open_is_missing(self):
        q = by_code(build_quotes(parse_mis(mis_with("0050", o="-")), {}, TODAY))["0050"]
        self.assertEqual(q["status"], "missing")
        self.assertIsNone(q["open"])
        self.assertIsNone(q["change"])
        self.assertIsNone(q["change_pct"])
        self.assertTrue(q["reason"])

    def test_stale_date_is_missing(self):
        q = by_code(build_quotes(parse_mis(mis_with("00631L", d="20261113")), {}, TODAY))["00631L"]
        self.assertEqual(q["status"], "missing")
        self.assertIsNone(q["open"])
        self.assertIsNone(q["prev_close"])


class ParseOfficialTest(unittest.TestCase):
    def test_parse_official_ignores_bad_rows(self):
        self.assertEqual(parse_official(OFFICIAL), {
            "0050": Decimal("102.50"), "00631L": Decimal("31.92"), "00685L": Decimal("10.40")})
        self.assertEqual(parse_official(None), {})
        self.assertEqual(parse_official([{"Code": "0050", "ClosingPrice": ""}, None, "x"]), {})


if __name__ == "__main__":
    unittest.main()
