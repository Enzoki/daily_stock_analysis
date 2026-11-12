# -*- coding: utf-8 -*-
"""證交所加權指數（官方）取代 Yahoo `^TWII`.

Yahoo 的 `^TWII` 落後一整個交易日：2026-11-12 17:00 UTC 時最新一列還是 11/11
（收 44,257.15），於是復盤把 11/11 的收盤寫成「今日」，再對上 11/12 的新聞
（加權 -2.63%）就自相矛盾。證交所 FMTQIK 同日就有 43,091.87 / -1,165.28。

No network: HTTP responses are stubbed with the real payload shapes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from data_provider.tw_index_provider import (
    build_twse_taiex_quote,
    fetch_twse_taiex_quote,
    fetch_twse_taiex_intraday_range,
)

# 真實 FMTQIK 回應（2026-11，欄位名以官方為準）
_FMTQIK = {
    "stat": "OK",
    "date": "20261112",
    "title": "115年11月市場成交資訊",
    "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
    "data": [
        ["115/11/10", "4,766,000,000", "900,000,000,000", "3,000,000", "42,711.92", "1,050.22"],
        ["115/11/11", "4,012,900,000", "980,000,000,000", "3,100,000", "44,257.15", "1,545.23"],
        ["115/11/12", "5,291,822,534", "959,189,903,408", "3,864,528", "43,091.87", "-1,165.28"],
    ],
}

_MI_5MINS = {
    "stat": "OK",
    "date": "20261112",
    "fields": ["時間", "發行量加權股價指數", "未含金融保險股指數"],
    "data": [
        ["09:00:00", "44,257.15", "1"],
        ["09:05:00", "44,400.10", "1"],
        ["12:30:00", "42,943.50", "1"],
        ["13:30:00", "43,091.87", "1"],
    ],
}


class _FakeResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    """依 params['date'] 或 URL 路徑回傳不同 payload。"""

    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for key, payload in self.by_url.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload if hasattr(payload, "raise_for_status") else _FakeResponse(payload)
        return _FakeResponse({"stat": "沒有資料"})


def test_build_quote_uses_latest_row_and_official_change() -> None:
    quote = build_twse_taiex_quote(_FMTQIK)

    assert quote is not None
    assert quote["code"] == "TWII"
    assert quote["as_of"] == "20261112"
    assert quote["current"] == pytest.approx(43091.87)
    assert quote["change"] == pytest.approx(-1165.28)
    assert quote["prev_close"] == pytest.approx(44257.15)
    assert quote["change_pct"] == pytest.approx(-1165.28 / 44257.15 * 100, rel=1e-6)
    # 官方成交金額/股數（Yahoo 這兩個欄位是 N/A）
    assert quote["amount"] == pytest.approx(959189903408)
    assert quote["volume"] == pytest.approx(5291822534)
    # FMTQIK 沒有日內高低 → 先留 0，由 5 秒統計補
    assert quote["open"] == 0.0 and quote["high"] == 0.0 and quote["low"] == 0.0


def test_build_quote_rejects_payload_without_taiex_fields() -> None:
    assert build_twse_taiex_quote({"fields": ["日期", "收盤指數"], "data": []}) is None
    assert build_twse_taiex_quote(None) is None
    assert build_twse_taiex_quote({"fields": [], "data": []}) is None


def test_build_quote_skips_rows_with_unparseable_close() -> None:
    payload = {
        "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
        "data": [["115/11/12", "0", "0", "0", "--", "0"]],
    }
    assert build_twse_taiex_quote(payload) is None


def test_fetch_quote_enriches_intraday_open_high_low() -> None:
    session = _FakeSession({"FMTQIK": _FMTQIK, "MI_5MINS_INDEX": _MI_5MINS})

    quote = fetch_twse_taiex_quote(session=session, timeout=5)

    assert quote is not None
    assert quote["open"] == pytest.approx(44257.15)
    assert quote["high"] == pytest.approx(44400.10)
    assert quote["low"] == pytest.approx(42943.50)
    assert quote["amplitude"] == pytest.approx((44400.10 - 42943.50) / 44257.15 * 100, rel=1e-6)


def test_fetch_quote_keeps_zero_range_when_intraday_close_disagrees() -> None:
    mismatched = dict(_MI_5MINS)
    mismatched["data"] = [["09:00:00", "40,000.00", "1"], ["13:30:00", "40,100.00", "1"]]
    session = _FakeSession({"FMTQIK": _FMTQIK, "MI_5MINS_INDEX": mismatched})

    quote = fetch_twse_taiex_quote(session=session, timeout=5)

    assert quote is not None
    assert quote["current"] == pytest.approx(43091.87)
    # 日内收盘对不上官方收盘 → 不用这批高低点，也不臆造
    assert quote["open"] == 0.0 and quote["high"] == 0.0 and quote["low"] == 0.0


def test_fetch_quote_is_fail_open_on_transport_errors() -> None:
    session = _FakeSession({"FMTQIK": RuntimeError("connection reset")})
    assert fetch_twse_taiex_quote(session=session, timeout=1, date="20261112") is None


def test_fetch_quote_falls_back_to_previous_month_when_empty() -> None:
    class _MonthAwareSession:
        def __init__(self):
            self.dates = []

        def get(self, url, **kwargs):
            self.dates.append(kwargs["params"]["date"])
            if "FMTQIK" not in url:
                return _FakeResponse({"fields": [], "data": []})
            if kwargs["params"]["date"].startswith("202612"):
                return _FakeResponse({"stat": "沒有資料", "fields": [], "data": []})
            return _FakeResponse(_FMTQIK)

    session = _MonthAwareSession()
    quote = fetch_twse_taiex_quote(session=session, timeout=5, date="20261201")

    assert quote is not None
    assert quote["as_of"] == "20261112"
    assert session.dates[0].startswith("202612")
    assert any(d.startswith("202611") for d in session.dates)


def test_intraday_range_is_fail_open() -> None:
    assert fetch_twse_taiex_intraday_range("20261112", session=_FakeSession({}), timeout=1) is None
    broken = _FakeSession({"MI_5MINS_INDEX": ValueError("not json")})
    assert fetch_twse_taiex_intraday_range("20261112", session=broken, timeout=1) is None


# ---------------------------------------------------------------------------
# fetcher 接線：官方優先、Yahoo 只補缺欄
# ---------------------------------------------------------------------------


class _Yahoo:
    """Fake yfinance module for _get_tw_main_indices."""

    def __init__(self, quote):
        self.quote = quote
        self.requested = []

    def Ticker(self, symbol):  # noqa: N802 - 模仿 yfinance API
        return self

    def history(self, *args, **kwargs):
        raise AssertionError("測試中不應真的打 yfinance")


def _fetcher_with(monkeypatch, taiex_yahoo, otc_quote=None):
    from data_provider.yfinance_fetcher import YfinanceFetcher

    fetcher = YfinanceFetcher()
    requested = []

    def fake_fetch(_yf, yf_code, name, return_code):
        requested.append((yf_code, return_code))
        return dict(taiex_yahoo) if return_code == "TWII" else None

    monkeypatch.setattr(fetcher, "_fetch_yf_ticker_data", fake_fetch)
    monkeypatch.setattr(
        "data_provider.yfinance_fetcher.fetch_twse_taiex_quote", lambda: dict(_official_quote())
    )
    monkeypatch.setattr(
        "data_provider.yfinance_fetcher.fetch_tw_otc_index_quote",
        lambda: dict(otc_quote) if otc_quote else None,
    )
    return fetcher, requested


def _official_quote():
    quote = build_twse_taiex_quote(_FMTQIK)
    quote.update({"open": 44257.15, "high": 44525.24, "low": 42943.50, "amplitude": 3.574})
    return quote


def test_fetcher_prefers_official_taiex_and_keeps_its_ohlc(monkeypatch) -> None:
    # Yahoo 仍在落後一日（11/11 收 44257.15），不可覆蓋官方 11/12 的數字
    yahoo = {"code": "TWII", "name": "台湾加权指数", "current": 44257.15,
             "open": 43135.76, "high": 44257.15, "low": 42708.05, "amplitude": 3.63,
             "change": 1545.23, "change_pct": 3.62, "prev_close": 42711.92,
             "volume": 4012900.0, "amount": 0.0}
    otc = {"code": "TWOII", "name": "台湾柜买指数", "as_of": "20261112",
           "current": 335.73, "change": -8.38, "change_pct": -2.44, "open": 344.58,
           "high": 352.57, "low": 335.11, "prev_close": 344.11, "volume": 0.0,
           "amount": 0.0, "amplitude": 5.07}
    fetcher, requested = _fetcher_with(monkeypatch, yahoo, otc)

    results = fetcher._get_tw_main_indices(object())

    assert results is not None
    assert [item["code"] for item in results] == ["TWII", "TWOII"]
    taiex = results[0]
    assert taiex["as_of"] == "20261112"
    assert taiex["current"] == pytest.approx(43091.87)
    assert taiex["change"] == pytest.approx(-1165.28)
    # 官方 OHLC 不被落後一日的 Yahoo 蓋掉
    assert taiex["open"] == pytest.approx(44257.15)
    assert taiex["high"] == pytest.approx(44525.24)
    assert results[1]["current"] == pytest.approx(335.73)


def test_fetcher_falls_back_to_yahoo_taiex_when_twse_fails(monkeypatch) -> None:
    from data_provider.yfinance_fetcher import YfinanceFetcher

    fetcher = YfinanceFetcher()
    yahoo = {"code": "TWII", "name": "台湾加权指数", "current": 44257.15, "open": 1.0,
             "high": 2.0, "low": 0.5, "amplitude": 1.0, "change": 1.0, "change_pct": 1.0,
             "prev_close": 1.0, "volume": 1.0, "amount": 0.0}

    monkeypatch.setattr(fetcher, "_fetch_yf_ticker_data", lambda *a, **k: dict(yahoo))
    monkeypatch.setattr("data_provider.yfinance_fetcher.fetch_twse_taiex_quote", lambda: None)
    monkeypatch.setattr("data_provider.yfinance_fetcher.fetch_tw_otc_index_quote", lambda: None)

    results = fetcher._get_tw_main_indices(object())

    assert results is not None
    assert results[0]["current"] == pytest.approx(44257.15)
    assert not results[0].get("as_of")
