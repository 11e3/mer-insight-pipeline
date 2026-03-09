"""
미국 노동통계국 (BLS) 데이터 수집.
  - CPI (소비자물가): CUUR0000SA0
  - 실업률: LNS14000000

Usage:
    python -m src.ingest.load_bls
"""

import asyncio
from datetime import date, timedelta

import asyncpg
import requests
import pandas as pd

from config.settings import DATABASE_URL, BLS_API_KEY

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
CPI_SERIES       = "CUUR0000SA0"   # CPI All Items (not seasonally adjusted)
UNEMPLOYMENT_SID = "LNS14000000"   # Unemployment Rate (seasonally adjusted)


def fetch_bls(series_ids: list[str], start_year: int, end_year: int) -> dict[str, pd.Series]:
    """BLS API v2로 월별 데이터 수집. key 없으면 v1으로 fallback."""
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear":   str(end_year),
    }
    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY

    try:
        resp = requests.post(BLS_API_URL, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [BLS 오류] {e}")
        return {}

    result = {}
    for series in data.get("Results", {}).get("series", []):
        sid  = series["seriesID"]
        rows = {}
        for obs in series.get("data", []):
            try:
                ym  = f"{obs['year']}-{obs['period'].replace('M','').zfill(2)}"
                val = float(obs["value"])
                rows[ym] = val
            except (ValueError, KeyError):
                continue
        result[sid] = pd.Series(rows, name=sid)

    return result


def compute_cpi_yoy(cpi_series: pd.Series) -> pd.Series:
    """CPI 월별 값 → 전년동월비 (%)."""
    df = cpi_series.sort_index().reset_index()
    df.columns = ["ym", "cpi"]
    df["prev_ym"] = df["ym"].apply(
        lambda x: f"{int(x[:4])-1}-{x[5:]}"
    )
    prev = df.set_index("ym")["cpi"]
    df["cpi_prev"] = df["prev_ym"].map(prev)
    df["yoy"] = (df["cpi"] - df["cpi_prev"]) / df["cpi_prev"] * 100
    return df.set_index("ym")["yoy"]


async def load_bls(start: str = "2015-01-01"):
    end = date.today().isoformat()
    start_year = int(start[:4])
    end_year   = int(end[:4])

    print(f"BLS 데이터 수집: {start_year} ~ {end_year}")

    # BLS API는 최대 10년 단위 → 10년씩 분할
    all_cpi  = {}
    all_unem = {}
    for yr in range(start_year, end_year + 1, 10):
        yr_end = min(yr + 9, end_year)
        print(f"  {yr} ~ {yr_end} 수집 중...")
        fetched = fetch_bls([CPI_SERIES, UNEMPLOYMENT_SID], yr, yr_end)
        if CPI_SERIES in fetched:
            all_cpi.update(fetched[CPI_SERIES].to_dict())
        if UNEMPLOYMENT_SID in fetched:
            all_unem.update(fetched[UNEMPLOYMENT_SID].to_dict())

    cpi_series  = pd.Series(all_cpi,  name="cpi")
    unem_series = pd.Series(all_unem, name="unemployment")
    cpi_yoy     = compute_cpi_yoy(cpi_series)

    conn = await asyncpg.connect(DATABASE_URL)

    # macro_daily: 해당 월의 모든 날짜에 forward-fill
    count = 0
    for ym, yoy in cpi_yoy.items():
        unem = unem_series.get(ym)
        # 해당 월 날짜 전체에 upsert
        await conn.execute("""
            UPDATE macro_daily
            SET
                us_cpi_yoy      = COALESCE($1, us_cpi_yoy),
                us_unemployment = COALESCE($2, us_unemployment)
            WHERE TO_CHAR(date, 'YYYY-MM') = $3
        """,
            float(yoy) if not pd.isna(yoy) else None,
            float(unem) if unem is not None and not pd.isna(unem) else None,
            ym,
        )
        count += 1

    await conn.close()
    print(f"BLS 적재 완료: {count}개월치 → macro_daily 갱신")


if __name__ == "__main__":
    asyncio.run(load_bls())
