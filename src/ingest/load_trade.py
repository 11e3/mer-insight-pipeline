"""
한국 수출입 통계 수집 (한국은행 ECOS API).
월별 수출입 총액 → trade_monthly 테이블 적재.

BOK ECOS 시리즈 (통관기준, 단위: 백만달러):
  - 수출: 902Y012 (9.1.3.4 한국 주요국 수출, 항목코드 KR)
  - 수입: 902Y013 (9.1.3.5 한국 주요국 수입, 항목코드 KR)

Usage:
    python -m src.ingest.load_trade
    python -m src.ingest.load_trade --start 2015-01
"""

import asyncio
import argparse
from datetime import date

import asyncpg
import requests

from config.settings import DATABASE_URL, BOK_API_KEY

# BOK ECOS API — 통관기준 수출입 총액 (단위: 백만달러)
# 9.1.3.4 한국 주요국 수출(수출입기준) / 9.1.3.5 한국 주요국 수입(수출입기준)
# 항목코드 KR = 한국 전체 합계
ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/{stat_code}/M/{start}/{end}/KR"

STAT_EXPORT = "902Y012"   # 수출 총액 (통관기준, 백만달러)
STAT_IMPORT = "902Y013"   # 수입 총액 (통관기준, 백만달러)


def _fetch_ecos(stat_code: str, start_ym: str, end_ym: str) -> dict[str, float]:
    """ECOS API → {ym: value} 딕셔너리 반환."""
    if not BOK_API_KEY:
        return {}
    start = start_ym.replace("-", "")
    end   = end_ym.replace("-", "")
    url = ECOS_BASE.format(
        api_key=BOK_API_KEY,
        stat_code=stat_code,
        start=start,
        end=end,
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        result = {}
        for r in rows:
            ym  = r.get("TIME", "")
            val = r.get("DATA_VALUE", "")
            if len(ym) == 6:
                ym_fmt = f"{ym[:4]}-{ym[4:]}"
                try:
                    result[ym_fmt] = float(val)
                except (ValueError, TypeError):
                    pass
        return result
    except Exception as e:
        print(f"  [ECOS 오류 {stat_code}] {e}")
        return {}


def _ym_range(start_ym: str, end_ym: str) -> list[str]:
    yms = []
    y, m = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    while (y, m) <= (ey, em):
        yms.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return yms


async def load_trade(start: str = "2015-01"):
    end = date.today().strftime("%Y-%m")
    print(f"수출입 통계 수집 (BOK ECOS): {start} ~ {end}")

    if not BOK_API_KEY:
        print("  [건너뜀] BOK_API_KEY 미설정")
        return

    print("  수출 데이터 수집 중...")
    exports = _fetch_ecos(STAT_EXPORT, start, end)
    print(f"  → {len(exports)}개월")

    print("  수입 데이터 수집 중...")
    imports = _fetch_ecos(STAT_IMPORT, start, end)
    print(f"  → {len(imports)}개월")

    if not exports and not imports:
        # ECOS 시리즈 코드가 맞지 않으면 yfinance의 무역지수로 대체
        print("  [주의] ECOS 데이터 없음 — 시리즈 코드 확인 필요")
        print("  BOK ECOS 통계 검색: https://ecos.bok.or.kr/")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    count = 0
    all_yms = set(exports) | set(imports)

    for ym in sorted(all_yms):
        exp = exports.get(ym)
        imp = imports.get(ym)
        bal = (exp - imp) if exp is not None and imp is not None else None

        await conn.execute("""
            INSERT INTO trade_monthly
                (ym, export_total, import_total, trade_balance)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (ym) DO UPDATE SET
                export_total  = COALESCE(EXCLUDED.export_total,  trade_monthly.export_total),
                import_total  = COALESCE(EXCLUDED.import_total,  trade_monthly.import_total),
                trade_balance = COALESCE(EXCLUDED.trade_balance, trade_monthly.trade_balance),
                updated_at    = NOW()
        """, ym, exp, imp, bal)
        count += 1

    await conn.close()
    print(f"수출입 적재 완료: {count}개월")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01")
    args = parser.parse_args()
    asyncio.run(load_trade(args.start))
