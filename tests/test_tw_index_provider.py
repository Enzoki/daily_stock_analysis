# -*- coding: utf-8 -*-
"""TPEx 柜买指数 fallback（Yahoo `^TWOII` 已下架）.

Covers ``data_provider/tw_index_provider.py`` parsing + the
``YfinanceFetcher._get_tw_main_indices`` wiring. No network: HTTP responses are
stubbed.
"""

from __future__ import annotations

import pytest

from data_provider.tw_index_provider import (
    build_tw_otc_index_quote,
    fetch_tw_otc_index_quote,
)

# Real payload shape (TPEx OpenAPI, verified live 2026-11):
#   [{"Date": "20261111", "Open": "333.39", "High": "344.11",
#     "Low": "333.05", "Close": "344.11", "Change": "11.14"}, ...]
_LIVE_ROWS = [
    {"Date": "20261109", "Open": "305.51", "High": "323.84", "Low": "305.51", "Close": "321.97", "Change": "17.95"},
    {"Date": "20261110", "Open": "325.41", "High": "338.52", "Low": "325.41", "Close": "332.97", "Change": "11.00"},
    {"Date": "20261111", "Open": "333.39", "High": "344.11", "Low": "333.05", "Close": "344.11", "Change": "11.14"},
]


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
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_build_quote_from_live_shape_uses_latest_row_and_change() -> None:
    quote = build_tw_otc_index_quote(_LIVE_ROWS)

    assert quote is not None
    assert quote["code"] == "TWOII"
    assert quote["name"] == "台湾柜买指数"
    assert quote["as_of"] == "20261111"
    assert quote["current"] == pytest.approx(344.11)
    assert quote["change"] == pytest.approx(11.14)
    assert quote["prev_close"] == pytest.approx(332.97)
    assert quote["change_pct"] == pytest.approx(3.3456, rel=1e-3)
    assert quote["open"] == pytest.approx(333.39)
    assert quote["high"] == pytest.approx(344.11)
    assert quote["low"] == pytest.approx(333.05)
    assert quote["amplitude"] == pytest.approx((344.11 - 333.05) / 332.97 * 100, rel=1e-4)
    # TPEx 指数 OpenAPI 不给成交量/金额 — 必须留 0（復盘显示 N/A），不可编造。
    assert quote["volume"] == 0.0
    assert quote["amount"] == 0.0


def test_unsorted_rows_are_sorted_by_date() -> None:
    shuffled = [_LIVE_ROWS[2], _LIVE_ROWS[0], _LIVE_ROWS[1]]
    quote = build_tw_otc_index_quote(shuffled)

    assert quote is not None
    assert quote["as_of"] == "20261111"
    assert quote["current"] == pytest.approx(344.11)


def test_rows_without_parseable_close_are_ignored() -> None:
    rows = [
        {"Date": "20261110", "Close": "--", "Change": "0"},
        {"Date": "20261111", "Close": "344.11", "Change": "11.14", "High": "344.11", "Low": "333.05"},
    ]
    quote = build_tw_otc_index_quote(rows)

    assert quote is not None
    assert quote["as_of"] == "20261111"


def test_single_row_without_change_falls_back_to_none() -> None:
    # 单列且缺 Change 时无法推昨收 → fail-open，不猜。
    assert build_tw_otc_index_quote([{"Date": "20261111", "Close": "344.11"}]) is None


def test_change_absent_uses_previous_row_close() -> None:
    rows = [
        {"Date": "20261110", "Close": "332.97"},
        {"Date": "20261111", "Close": "344.11", "High": "344.11", "Low": "333.05"},
    ]
    quote = build_tw_otc_index_quote(rows)

    assert quote is not None
    assert quote["change"] == pytest.approx(11.14)
    assert quote["prev_close"] == pytest.approx(332.97)


def test_minguo_date_is_tolerated() -> None:
    rows = [
        {"Date": "1151110", "Close": "332.97", "Change": "11.00"},
        {"Date": "1151111", "Close": "344.11", "Change": "11.14"},
    ]
    quote = build_tw_otc_index_quote(rows)

    assert quote is not None
    assert quote["as_of"] == "20261111"


def test_empty_or_garbage_payload_returns_none() -> None:
    assert build_tw_otc_index_quote([]) is None
    assert build_tw_otc_index_quote(None) is None
    assert build_tw_otc_index_quote(["not-a-row", 42]) is None


def test_fetch_returns_quote_on_success_and_sends_ua() -> None:
    session = _FakeSession(_FakeResponse(_LIVE_ROWS))

    quote = fetch_tw_otc_index_quote(session=session, timeout=5)

    assert quote is not None
    assert quote["current"] == pytest.approx(344.11)
    url, kwargs = session.calls[0]
    assert url.endswith("/openapi/v1/tpex_index")
    assert kwargs["timeout"] == 5
    assert "User-Agent" in kwargs["headers"]


@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("connection reset"),
        _FakeResponse(None, status_error=RuntimeError("HTTP 503")),
        _FakeResponse(ValueError("not json")),
    ],
)
def test_fetch_is_fail_open_on_transport_errors(response) -> None:
    assert fetch_tw_otc_index_quote(session=_FakeSession(response), timeout=1) is None


def test_fetch_is_fail_open_on_payload_without_dates() -> None:
    assert fetch_tw_otc_index_quote(session=_FakeSession(_FakeResponse([{"Foo": "bar"}])), timeout=1) is None


def test_yfinance_tw_indices_prefers_tpex_for_otc(monkeypatch) -> None:
    from data_provider.yfinance_fetcher import YfinanceFetcher

    fetcher = YfinanceFetcher()
    requested = []

    def fake_fetch(_yf, yf_code, name, return_code):
        requested.append((yf_code, return_code))
        return {"code": return_code, "name": name, "current": 100.0}

    otc_quote = {"code": "TWOII", "name": "台湾柜买指数", "current": 344.11}
    monkeypatch.setattr(fetcher, "_fetch_yf_ticker_data", fake_fetch)
    monkeypatch.setattr(
        "data_provider.yfinance_fetcher.fetch_tw_otc_index_quote", lambda: dict(otc_quote)
    )

    results = fetcher._get_tw_main_indices(object())

    assert results is not None
    assert [item["code"] for item in results] == ["TWII", "TWOII"]
    assert results[1]["current"] == pytest.approx(344.11)
    # ^TWOII 已下架，TPEx 成功时不应再去问 Yahoo。
    assert ("^TWOII", "TWOII") not in requested


def test_yfinance_tw_indices_falls_back_to_yahoo_when_tpex_fails(monkeypatch) -> None:
    from data_provider.yfinance_fetcher import YfinanceFetcher

    fetcher = YfinanceFetcher()
    requested = []

    def fake_fetch(_yf, yf_code, name, return_code):
        requested.append((yf_code, return_code))
        return {"code": return_code, "name": name, "current": 100.0}

    monkeypatch.setattr(fetcher, "_fetch_yf_ticker_data", fake_fetch)
    monkeypatch.setattr("data_provider.yfinance_fetcher.fetch_tw_otc_index_quote", lambda: None)

    results = fetcher._get_tw_main_indices(object())

    assert results is not None
    assert [item["code"] for item in results] == ["TWII", "TWOII"]
    assert ("^TWOII", "TWOII") in requested


def test_yfinance_tw_indices_returns_none_when_both_legs_fail(monkeypatch) -> None:
    from data_provider.yfinance_fetcher import YfinanceFetcher

    fetcher = YfinanceFetcher()

    monkeypatch.setattr(fetcher, "_fetch_yf_ticker_data", lambda *a, **k: None)
    monkeypatch.setattr("data_provider.yfinance_fetcher.fetch_tw_otc_index_quote", lambda: None)

    assert fetcher._get_tw_main_indices(object()) is None
