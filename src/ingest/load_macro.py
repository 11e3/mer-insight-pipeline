"""
매크로 데이터 수집 → macro_daily 테이블 적재.

소스:
  - FRED API   : VIX, 미 10년물, WTI, BTC, Fed Funds Rate, CPI, 실업률
  - BOK ECOS   : 원/달러 환율, KOSPI, KOSDAQ, 기준금리

Usage:
    python -m src.ingest.load_macro
    python -m src.ingest.load_macro --start 2015-01-01 --end 2026-03-14
"""

import asyncio
import argparse
from datetime import date, timedelta

import asyncpg
import pandas as pd
import requests

from config.settings import DATABASE_URL, FRED_API_KEY, BOK_API_KEY

# ─── FRED ─────────────────────────────────────────────────────────────────────

FRED_SERIES = {
    "vix":            "VIXCLS",      # CBOE VIX
    "us_10y":         "DGS10",       # 미 10년물 국채 수익률
    "wti":            "DCOILWTICO",  # WTI 원유
    "btc_usd":        "CBBTCUSD",    # 비트코인
    "fed_funds_rate": "FEDFUNDS",    # Fed Funds Rate (월별 → ffill)
    "us_cpi_yoy":     "CPIAUCSL",    # CPI (월별, YoY 변환)
    "us_unemployment":"UNRATE",      # 실업률 (월별 → ffill)
}

# ─── BOK ECOS ─────────────────────────────────────────────────────────────────

BOK_SERIES = {
    "usd_krw":      ("731Y001", "0000001"),  # 원/달러 매매기준율
    "kospi":        ("802Y001", "0001000"),  # KOSPI 지수
    "kosdaq":       ("802Y001", "0089000"),  # KOSDAQ 지수
    "kr_base_rate": ("722Y001", "0101000"),  # 한국은행 기준금리
}

BOK_BASE = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/2000/{stat}/D/{start}/{end}/{item}"


def _fred_fetch(series_id: str, start: str, end: str, freq: str = "d") -> pd.Series:
    if not FRED_API_KEY:
        return pd.Series(dtype=float)
    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&observation_start={start}&observation_end={end}"
            f"&api_key={FRED_API_KEY}&file_type=json&frequency={freq}"
        )
        data = requests.get(url, timeout=15).json()
        obs = data.get("observations", [])
        s = pd.Series(
            {o["date"]: float(o["value"]) for o in obs if o["value"] != "."},
            name=series_id,
            dtype=float,
        )
        s.index = pd.to_datetime(s.index).date
        return s
    except Exception as e:
        print(f"  [FRED {series_id} 오류] {e}")
        return pd.Series(dtype=float)


def _bok_fetch(stat_code: str, item_code: str, start: str, end: str) -> pd.Series:
    if not BOK_API_KEY:
        return pd.Series(dtype=float)
    try:
        start_fmt = start.replace("-", "")
        end_fmt   = end.replace("-", "")
        url = BOK_BASE.format(
            key=BOK_API_KEY, stat=stat_code, item=item_code,
            start=start_fmt, end=end_fmt,
        )
        data = requests.get(url, timeout=15).json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        s = pd.Series(
            {r["TIME"]: float(r["DATA_VALUE"]) for r in rows if r.get("DATA_VALUE")},
            dtype=float,
        )
        s.index = pd.to_datetime(s.index, format="%Y%m%d").date
        return s
    except Exception as e:
        print(f"  [BOK {stat_code}/{item_code} 오류] {e}")
        return pd.Series(dtype=float)


def fetch_all(start: str, end: str) -> pd.DataFrame:
    """모든 소스에서 수집 → 날짜 인덱스 DataFrame."""
    # 날짜 범위 생성 (주말 포함)
    idx = pd.date_range(start, end, freq="D").date
    df  = pd.DataFrame(index=idx)

    # BOK 수집
    print("  [1/2] BOK ECOS 수집 중...")
    for col, (stat, item) in BOK_SERIES.items():
        s = _bok_fetch(stat, item, start, end)
        if not s.empty:
            df[col] = s.reindex(idx).ffill()
            print(f"    {col}: {len(s)}건")

    # FRED 수집
    print("  [2/2] FRED 수집 중...")
    for col, series_id in FRED_SERIES.items():
        if col in ("us_cpi_yoy", "us_unemployment", "fed_funds_rate"):
            # 월별 데이터 → 일별 ffill
            s = _fred_fetch(series_id, start, end, freq="m")
        else:
            s = _fred_fetch(series_id, start, end, freq="d")

        if not s.empty:
            if col == "us_cpi_yoy":
                # 전년 동월 대비 YoY 계산
                s_yoy = s.pct_change(12) * 100
                df[col] = s_yoy.reindex(idx).ffill()
            else:
                df[col] = s.reindex(idx).ffill()
            print(f"    {col}: {len(s)}건")

    return df


async def upsert_macro(conn: asyncpg.Connection, df: pd.DataFrame) -> int:
    df = df.where(pd.notnull(df), None)
    count = 0
    for dt, row in df.iterrows():
        await conn.execute("""
            INSERT INTO macro_daily
                (date, kospi, kosdaq, usd_krw, us_10y, kr_base_rate,
                 fed_funds_rate, wti, btc_usd, vix, us_cpi_yoy, us_unemployment)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (date) DO UPDATE SET
                kospi          = COALESCE(EXCLUDED.kospi,          macro_daily.kospi),
                kosdaq         = COALESCE(EXCLUDED.kosdaq,         macro_daily.kosdaq),
                usd_krw        = COALESCE(EXCLUDED.usd_krw,        macro_daily.usd_krw),
                us_10y         = COALESCE(EXCLUDED.us_10y,         macro_daily.us_10y),
                kr_base_rate   = COALESCE(EXCLUDED.kr_base_rate,   macro_daily.kr_base_rate),
                fed_funds_rate = COALESCE(EXCLUDED.fed_funds_rate, macro_daily.fed_funds_rate),
                wti            = COALESCE(EXCLUDED.wti,            macro_daily.wti),
                btc_usd        = COALESCE(EXCLUDED.btc_usd,        macro_daily.btc_usd),
                vix            = COALESCE(EXCLUDED.vix,            macro_daily.vix),
                us_cpi_yoy     = COALESCE(EXCLUDED.us_cpi_yoy,     macro_daily.us_cpi_yoy),
                us_unemployment= COALESCE(EXCLUDED.us_unemployment, macro_daily.us_unemployment)
        """,
            dt,
            _f(row, "kospi"),   _f(row, "kosdaq"),  _f(row, "usd_krw"),
            _f(row, "us_10y"),  _f(row, "kr_base_rate"),
            _f(row, "fed_funds_rate"),
            _f(row, "wti"),     _f(row, "btc_usd"), _f(row, "vix"),
            _f(row, "us_cpi_yoy"), _f(row, "us_unemployment"),
        )
        count += 1
    return count


def _f(row, col) -> float | None:
    v = row.get(col)
    if v is None:
        return None
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


async def load_macro(start: str = "2015-01-01", end: str | None = None):
    end = end or date.today().isoformat()
    print(f"매크로 데이터 수집: {start} ~ {end}\n")

    df = fetch_all(start, end)
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
