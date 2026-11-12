# -*- coding: utf-8 -*-
"""Taiwan index sources for the TW market recap (TAIEX + TPEx OTC).

Two official feeds, both fail-open:

**加權指數 (TAIEX)** — TWSE ``FMTQIK`` (每日市場成交資訊)::

    GET https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=YYYYMMDD&response=json
    {"fields": ["日期", "成交股數", "成交金額", "成交筆數",
                "發行量加權股價指數", "漲跌點數"],
     "data": [["115/11/12", "5,291,822,534", "959,189,903,408", "3,864,528",
               "43,091.87", "-1,165.28"], ...]}

Yahoo ``^TWII`` lags by a full trading day (2026-11-12 17:00 UTC still only had
the 11/11 row), so the recap labelled 11/11's close as "today" and contradicted
the same-day news. TWSE is authoritative and also carries 成交金額, which Yahoo
leaves as N/A.

**櫃買指數 (OTC)** — Yahoo delisted ``^TWOII`` ("No data found, symbol may be
delisted"), so the OTC leg comes from TPEx OpenAPI (same source family the TW
institutional fetcher already consumes)::

    GET https://www.tpex.org.tw/openapi/v1/tpex_index
    [{"Date": "20261111", "Open": "333.39", "High": "344.11",
      "Low": "333.05", "Close": "344.11", "Change": "11.14"}, ...]

Both feeds are government open data (OGDL v1). Failure policy is **fail-open**:
any HTTP / parsing / shape problem returns ``None`` so the recap runs without
that leg instead of failing the run — the prompt already forbids inventing
missing index data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

from data_provider.tw_institutional_fetcher import minguo_to_ad

logger = logging.getLogger(__name__)

TPEX_INDEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_index"
TWSE_FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
TWSE_MI_5MINS_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_INDEX"
TW_OTC_INDEX_CODE = "TWOII"
TW_OTC_INDEX_NAME = "台湾柜买指数"
TW_TAIEX_CODE = "TWII"
TW_TAIEX_NAME = "台湾加权指数"
REQUEST_TIMEOUT_SECONDS = 20

_TAIEX_CLOSE_FIELD = "發行量加權股價指數"
_TAIEX_CHANGE_FIELD = "漲跌點數"
_TAIEX_AMOUNT_FIELD = "成交金額"
_TAIEX_VOLUME_FIELD = "成交股數"
_TAIEX_DATE_FIELD = "日期"

_TPE = timezone(timedelta(hours=8))


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


# ---------------------------------------------------------------------------
# 加權指數 (TAIEX) — 證交所 FMTQIK（每日市場成交資訊）
# ---------------------------------------------------------------------------


def _roc_slash_date_to_iso(value: Any) -> Optional[str]:
    """``115/11/12`` -> ``20261112``；已是西元 8 碼者原樣回傳。"""

    text = str(value or "").strip()
    parts = text.split("/")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = (int(part) for part in parts)
        if year < 1900:
            year += 1911
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return f"{year:04d}{month:02d}{day:02d}"
    if text.isdigit() and len(text) == 8:
        return text
    return None


def build_twse_taiex_quote(payload: Any) -> Optional[Dict[str, Any]]:
    """把 FMTQIK 回應轉成 quote dict（取最新一列交易日）。

    FMTQIK 只提供收盤與漲跌點數（沒有日內開高低），因此 ``open``/``high``/
    ``low``/``amplitude`` 留 0，由呼叫端在有同日 Yahoo 資料時補上；成交金額與
    成交股數是官方值，直接採用（Yahoo 這兩個欄位是 N/A）。
    """

    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields") or []
    rows = payload.get("data") or []

    try:
        i_date = fields.index(_TAIEX_DATE_FIELD)
        i_close = fields.index(_TAIEX_CLOSE_FIELD)
        i_change = fields.index(_TAIEX_CHANGE_FIELD)
    except (ValueError, TypeError):
        return None
    i_amount = fields.index(_TAIEX_AMOUNT_FIELD) if _TAIEX_AMOUNT_FIELD in fields else None
    i_volume = fields.index(_TAIEX_VOLUME_FIELD) if _TAIEX_VOLUME_FIELD in fields else None

    parsed: List[tuple] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(i_close, i_change):
            continue
        date = _roc_slash_date_to_iso(row[i_date])
        close = _to_float(row[i_close])
        if date is None or close is None or close <= 0:
            continue
        parsed.append((date, close, _to_float(row[i_change]), row))
    if not parsed:
        return None

    parsed.sort(key=lambda item: item[0])
    date, close, change, row = parsed[-1]
    if change is None:
        if len(parsed) < 2:
            return None
        change = close - parsed[-2][1]
    prev_close = close - change
    if prev_close <= 0:
        return None

    amount = _to_float(row[i_amount]) if i_amount is not None and len(row) > i_amount else None
    volume = _to_float(row[i_volume]) if i_volume is not None and len(row) > i_volume else None

    return {
        "code": TW_TAIEX_CODE,
        "name": TW_TAIEX_NAME,
        "as_of": date,
        "current": close,
        "change": change,
        "change_pct": change / prev_close * 100,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "prev_close": prev_close,
        "volume": volume if volume is not None else 0.0,
        "amount": amount if amount is not None else 0.0,
        "amplitude": 0.0,
    }


def fetch_twse_taiex_quote(
    *,
    date: Optional[str] = None,
    url: str = TWSE_FMTQIK_URL,
    timeout: Optional[float] = None,
    session: Any = None,
) -> Optional[Dict[str, Any]]:
    """取證交所加權指數最新收盤；任何錯誤都回 ``None``（fail-open）。

    證交所對非交易日/未來日期回 ``{"stat": "..."}`` 或空資料，此時往回到前一個
    月份再試一次（跨月連假），仍失敗就放棄，由呼叫端退回 Yahoo。
    """

    http = session or requests
    today = datetime.now(_TPE).strftime("%Y%m%d")
    first = (date or today)[:8]
    months = [first]
    try:
        year, month = int(first[:4]), int(first[4:6])
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        months.append(f"{prev_year:04d}{prev_month:02d}01")
    except (ValueError, IndexError):
        pass

    for query in months:
        try:
            response = http.get(
                url,
                params={"date": query, "response": "json"},
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=timeout if timeout is not None else REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # network / HTTP / JSON — fail-open
            logger.warning("[TW-TAIEX] 證交所加權指數查詢失敗 date=%s: %s", query, exc)
            continue

        quote = build_twse_taiex_quote(payload)
        if quote is None:
            logger.warning("[TW-TAIEX] 證交所回應無可用加權指數資料 date=%s", query)
            continue

        intraday = fetch_twse_taiex_intraday_range(quote["as_of"], session=session, timeout=timeout)
        if intraday and abs(intraday["close"] - quote["current"]) <= max(0.05, quote["current"] * 0.0005):
            quote["open"] = intraday["open"]
            quote["high"] = intraday["high"]
            quote["low"] = intraday["low"]
            quote["amplitude"] = (
                (intraday["high"] - intraday["low"]) / quote["prev_close"] * 100
                if quote["prev_close"]
                else 0.0
            )

        logger.info(
            "[TW-TAIEX] 加權指數獲取成功 as_of=%s close=%.2f change=%.2f (%.2f%%)",
            quote.get("as_of"),
            quote["current"],
            quote["change"],
            quote["change_pct"],
        )
        return quote

    return None


def fetch_twse_taiex_intraday_range(
    date: str,
    *,
    url: str = TWSE_MI_5MINS_URL,
    timeout: Optional[float] = None,
    session: Any = None,
) -> Optional[Dict[str, float]]:
    """用證交所「每5秒指數統計」補 TAIEX 的開高低（FMTQIK 只有收盤）。

    回傳 ``{"open":…, "high":…, "low":…, "close":…}``；任何錯誤回 ``None``，
    由呼叫端保留 0（拒絕臆造日內資料）。
    """

    http = session or requests
    try:
        response = http.get(
            url,
            params={"date": date, "response": "json"},
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=timeout if timeout is not None else REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # fail-open
        logger.warning("[TW-TAIEX] 每5秒指數統計查詢失敗 date=%s: %s", date, exc)
        return None

    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    try:
        i_value = fields.index(_TAIEX_CLOSE_FIELD)
    except (ValueError, TypeError):
        return None

    values: List[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= i_value:
            continue
        value = _to_float(row[i_value])
        if value is None or value <= 0:
            continue
        values.append(value)
    if not values:
        return None

    return {
        "open": values[0],
        "high": max(values),
        "low": min(values),
        "close": values[-1],
    }
