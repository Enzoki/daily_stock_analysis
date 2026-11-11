# -*- coding: utf-8 -*-
"""TPEx (Taipei Exchange) OTC index fallback for the Taiwan market recap.

Yahoo Finance delisted ``^TWOII`` (the TPEx/OTC weighted index) — every call now
returns "No data found, symbol may be delisted" — so the TW market recap lost the
櫃買 leg and could only talk about 加權指數.

TPEx publishes the same series through its OpenAPI::

    GET https://www.tpex.org.tw/openapi/v1/tpex_index
    [{"Date": "20261111", "Open": "333.39", "High": "344.11",
      "Low": "333.05", "Close": "344.11", "Change": "11.14"}, ...]

Government open data under OGDL v1 (same source family the TW institutional
fetcher already consumes, ``data_provider/tw_institutional_fetcher.py``).

Failure policy is **fail-open**: any HTTP / parsing / shape problem returns
``None`` so the recap runs without the OTC leg instead of failing the run — the
prompt already forbids inventing missing index data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

import requests

from data_provider.tw_institutional_fetcher import minguo_to_ad

logger = logging.getLogger(__name__)

TPEX_INDEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_index"
TW_OTC_INDEX_CODE = "TWOII"
TW_OTC_INDEX_NAME = "台湾柜买指数"
REQUEST_TIMEOUT_SECONDS = 20

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _to_float(value: Any) -> Optional[float]:
    """Parse a TPEx numeric cell, returning ``None`` for empty/placeholder cells."""

    try:
        text = str(value).replace(",", "").replace(" ", "").strip()
    except (TypeError, ValueError):
        return None
    if text in ("", "-", "--", "—"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_date(value: Any) -> Optional[str]:
    """Normalize a TPEx date cell to 西元 ``YYYYMMDD``.

    The OTC index feed serves 8-digit 西元 dates (``20261111``); the 7-digit 民國
    form (``1151111``) is tolerated for consistency with the institutional feed.
    """

    text = str(value or "").strip()
    if not text.isdigit():
        return None
    if len(text) == 8:
        return text
    if len(text) == 7:
        return minguo_to_ad(text)
    return None


def _sorted_rows(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Keep rows with a usable date/close pair and sort them oldest → newest."""

    parsed: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date = _normalize_date(row.get("Date"))
        close = _to_float(row.get("Close"))
        if date is None or close is None or close <= 0:
            continue
        parsed.append({"date": date, "row": row, "close": close})
    parsed.sort(key=lambda item: item["date"])
    return parsed


def build_tw_otc_index_quote(rows: Optional[Iterable[Any]]) -> Optional[Dict[str, Any]]:
    """Build one market-index quote dict from TPEx OTC index rows.

    The returned mapping mirrors ``YfinanceFetcher._fetch_yf_ticker_data`` so the
    market recap can consume it without a special case.
    """

    ordered = _sorted_rows(rows)
    if not ordered:
        return None

    latest = ordered[-1]
    row = latest["row"]
    close = latest["close"]

    change = _to_float(row.get("Change"))
    if change is None:
        if len(ordered) < 2:
            return None
        change = close - ordered[-2]["close"]
    prev_close = close - change
    if prev_close <= 0:
        return None

    open_price = _to_float(row.get("Open"))
    high = _to_float(row.get("High"))
    low = _to_float(row.get("Low"))
    amplitude = 0.0
    if high is not None and low is not None and high >= low:
        amplitude = (high - low) / prev_close * 100

    return {
        "code": TW_OTC_INDEX_CODE,
        "name": TW_OTC_INDEX_NAME,
        "as_of": latest["date"],
        "current": close,
        "change": change,
        "change_pct": (change / prev_close * 100) if prev_close else 0.0,
        "open": open_price if open_price is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "prev_close": prev_close,
        # TPEx 的指數 OpenAPI 不提供成交量/成交金額；填 0 與 yfinance 台股路徑一致，
        # 大盤復盤的成交額欄位會顯示 N/A，而不是編造數字。
        "volume": 0.0,
        "amount": 0.0,
        "amplitude": amplitude,
    }


def fetch_tw_otc_index_quote(
    *,
    url: str = TPEX_INDEX_URL,
    timeout: Optional[float] = None,
    session: Any = None,
) -> Optional[Dict[str, Any]]:
    """Fetch the latest TPEx OTC index quote; ``None`` on any failure (fail-open)."""

    http = session or requests
    try:
        response = http.get(
            url,
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=timeout if timeout is not None else REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # network / HTTP / JSON — all fail-open
        logger.warning("[TW-OTC] TPEx 柜买指数获取失败，本次不注入柜买指数: %s", exc)
        return None

    quote = build_tw_otc_index_quote(payload)
    if quote is None:
        logger.warning("[TW-OTC] TPEx 柜买指数响应无可用的 Date/Close 资料，本次不注入")
        return None

    logger.info(
        "[TW-OTC] 柜买指数获取成功 date=%s close=%.2f change=%.2f",
        quote.get("as_of"),
        quote["current"],
        quote["change"],
    )
    return quote
