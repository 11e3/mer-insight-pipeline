"""
매크로 데이터 수집 → macro_daily 테이블 적재.
  - yfinance: KOSPI, KOSDAQ, USD/KRW, 미국10년물, WTI, BTC, VIX
  - FRED API: Fed Funds Rate
  - 한국은행 API: 기준금리

Usage:
    python -m src.ingest.load_macro
    python -m src.ingest.load_macro --start 2015-01-01 --end 2026-03-08
"""

import asyncio
import argparse
from datetime import date, timedelta

import asyncpg
import pandas as pd
import yfinance as yf
import requests

from config.settings import DATABASE_URL, FRED_API_KEY, BOK_API_KEY, BOK_BASE_RATE_URL


YFINANCE_TICKERS = {
    "kospi":   "^KS11",
    "kosdaq":  "^KQ11",
    "usd_krw": "KRW=X",
    "us_10y":  "^TNX",
    "wti":     "CL=F",
    "btc_usd": "BTC-USD",
    "vix":     "^VIX",
}


def fetch_yfinance(start: str, end: str) -> pd.DataFrame:
    """yfinance에서 일별 종가 수집."""
    tickers = list(YFINANCE_TICKERS.values())
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)

    # Close 가격만 추출
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]]

    rename_map = {v: k for k, v in YFINANCE_TICKERS.items()}
    close = close.rename(columns=rename_map)
    close.index = pd.to_datetime(close.index).date
    return close


def fetch_fred_fedfunds(start: str, end: str) -> pd.Series:
    """FRED에서 Fed Funds Rate 수집."""
    if not FRED_API_KEY:
        return pd.Series(dtype=float)
    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=FEDFUNDS&observation_start={start}&observation_end={end}"
            f"&api_key={FRED_API_KEY}&file_type=json&frequency=d"
        )
        data = requests.get(url, timeout=10).json()
        obs = data.get("observations", [])
        series = pd.Series(
            {o["date"]: float(o["value"]) for o in obs if o["value"] != "."},
            name="fed_funds_rate",
        )
        series.index = pd.to_datetime(series.index).date
        return series
    except Exception as e:
        print(f"  [FRED 오류] {e}")
        return pd.Series(dtype=float)


def fetch_bok_base_rate(start: str, end: str) -> pd.Series:
    """한국은행 API에서 기준금리 수집."""
    if not BOK_API_KEY:
        return pd.Series(dtype=float)
    try:
        start_fmt = start.replace("-", "")
        end_fmt   = end.replace("-", "")
        url = BOK_BASE_RATE_URL.format(
            api_key=BOK_API_KEY, start=start_fmt, end=end_fmt
        )
        data = requests.get(url, timeout=10).json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        series = pd.Series(
            {r["TIME"]: float(r["DATA_VALUE"]) for r in rows},
            name="kr_base_rate",
        )
        # TIME 형식: "20240101" → date
        series.index = pd.to_datetime(series.index, format="%Y%m%d").date
        return series
    except Exception as e:
        print(f"  [BOK 오류] {e}")
        return pd.Series(dtype=float)


async def upsert_macro(conn: asyncpg.Connection, df: pd.DataFrame):
    """DataFrame → macro_daily 테이블 upsert."""
    rows = df.reset_index().rename(columns={"index": "date"})
    count = 0
    for _, row in rows.iterrows():
        await conn.execute("""
            INSERT INTO macro_daily
                (date, kospi, kosdaq, usd_krw, us_10y, kr_base_rate,
                 wti, btc_usd, vix, fed_funds_rate)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (date) DO UPDATE SET
                kospi          = COALESCE(EXCLUDED.kospi, macro_daily.kospi),
                kosdaq         = COALESCE(EXCLUDED.kosdaq, macro_daily.kosdaq),
                usd_krw        = COALESCE(EXCLUDED.usd_krw, macro_daily.usd_krw),
                us_10y         = COALESCE(EXCLUDED.us_10y, macro_daily.us_10y),
                kr_base_rate   = COALESCE(EXCLUDED.kr_base_rate, macro_daily.kr_base_rate),
                wti            = COALESCE(EXCLUDED.wti, macro_daily.wti),
                btc_usd        = COALESCE(EXCLUDED.btc_usd, macro_daily.btc_usd),
                vix            = COALESCE(EXCLUDED.vix, macro_daily.vix),
                fed_funds_rate = COALESCE(EXCLUDED.fed_funds_rate, macro_daily.fed_funds_rate)
        """,
            row.get("date"),
            _float(row, "kospi"), _float(row, "kosdaq"), _float(row, "usd_krw"),
            _float(row, "us_10y"), _float(row, "kr_base_rate"),
            _float(row, "wti"), _float(row, "btc_usd"), _float(row, "vix"),
            _float(row, "fed_funds_rate"),
        )
        count += 1
    return count


def _float(row, col) -> float | None:
    val = row.get(col)
    if val is None or (hasattr(val, "__class__") and str(val) == "nan"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def load_macro(start: str = "2015-01-01", end: str | None = None):
    end = end or date.today().isoformat()
    print(f"매크로 데이터 수집: {start} ~ {end}\n")

    # yfinance
    print("  [1/3] yfinance 수집 중...")
    df = fetch_yfinance(start, end)

    # FRED
    print("  [2/3] FRED (Fed Funds Rate) 수집 중...")
    fed = fetch_fred_fedfunds(start, end)
    if not fed.empty:
        df["fed_funds_rate"] = fed.reindex(df.index).ffill()

    # 한국은행
    print("  [3/3] 한국은행 기준금리 수집 중...")
    bok = fetch_bok_base_rate(start, end)
    if not bok.empty:
        df["kr_base_rate"] = bok.reindex(df.index).ffill()

    print(f"\n  수집 완료: {len(df)}일치 데이터")

    conn = await asyncpg.connect(DATABASE_URL)
    count = await upsert_macro(conn, df)
    await conn.close()

    print(f"  DB 적재 완료: {count}행")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end",   default=None)
    args = parser.parse_args()
    asyncio.run(load_macro(args.start, args.end))
