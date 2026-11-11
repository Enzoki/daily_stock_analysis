# -*- coding: utf-8 -*-
"""``_fetch_yf_ticker_data`` 涨跌幅：Yahoo 只回一列时的退化修正.

Yahoo 对 ``period='2d'`` 有时只回传当日一列（台湾加权指数 ``^TWII`` 常见），
旧代码此时把 ``prev_row`` 当成 ``today_row``，涨跌被算成 ``0.00%``，让大盘复盘
误判「加权指数平盘」。修正后单列时改抓 ``5d`` 窗口取前一交易日。
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.yfinance_fetcher import YfinanceFetcher


def _yf(ticker):
    """Fake ``yfinance`` module: ``_fetch_yf_ticker_data`` takes it as an argument."""
    return SimpleNamespace(Ticker=lambda _symbol: ticker)


def _frame(closes):
    idx = pd.to_datetime([f"2026-11-{d:02d}" for d in range(4, 4 + len(closes))])
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000.0 for _ in closes],
        },
        index=idx,
    )


class _FakeTicker:
    def __init__(self, by_period):
        self.by_period = by_period
        self.periods_requested = []

    def history(self, period="1mo"):
        self.periods_requested.append(period)
        return self.by_period.get(period, pd.DataFrame())


def test_single_row_two_day_window_widens_to_five_days(monkeypatch) -> None:
    ticker = _FakeTicker({"2d": _frame([42711.92]), "5d": _frame([41574.72, 39701.64, 41661.70, 42711.92])})
    quote = YfinanceFetcher()._fetch_yf_ticker_data(_yf(ticker), "^TWII", "台湾加权指数", "TWII")

    assert quote is not None
    assert ticker.periods_requested == ["2d", "5d"]
    assert quote["current"] == pytest.approx(42711.92)
    assert quote["prev_close"] == pytest.approx(41661.70)
    assert quote["change"] == pytest.approx(1050.22, rel=1e-6)
    assert quote["change_pct"] == pytest.approx(1050.22 / 41661.70 * 100, rel=1e-6)


def test_two_rows_keep_original_behaviour(monkeypatch) -> None:
    ticker = _FakeTicker({"2d": _frame([41661.70, 42711.92])})
    quote = YfinanceFetcher()._fetch_yf_ticker_data(_yf(ticker), "^TWII", "台湾加权指数", "TWII")

    assert quote is not None
    assert ticker.periods_requested == ["2d"]
    assert quote["change"] == pytest.approx(1050.22, rel=1e-6)


def test_single_row_without_wider_window_stays_zero_change(monkeypatch) -> None:
    ticker = _FakeTicker({"2d": _frame([42711.92]), "5d": _frame([42711.92])})
    quote = YfinanceFetcher()._fetch_yf_ticker_data(_yf(ticker), "^TWII", "台湾加权指数", "TWII")

    assert quote is not None
    assert quote["change"] == 0.0


def test_empty_frames_return_none(monkeypatch) -> None:
    ticker = _FakeTicker({"2d": pd.DataFrame(), "5d": pd.DataFrame()})
    monkeypatch.setattr("yfinance.Ticker", lambda _symbol: ticker)

    assert YfinanceFetcher()._fetch_yf_ticker_data(_yf(ticker), "^TWII", "台湾加权指数", "TWII") is None
