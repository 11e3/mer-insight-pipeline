"""
국토교통부 아파트 매매 실거래가 수집 (공공데이터포털 API).

API: 아파트매매 실거래가 정보
URL: http://openapi.molit.go.kr/OpenAPI_ToolInstallPackage/service/rest/RTMSOBJSvc/getRTMSDataSvcAptTrade

Usage:
    python -m src.ingest.load_realestate
    python -m src.ingest.load_realestate --start 2015-01
"""

import asyncio
import argparse
import time
from collections import defaultdict
from datetime import date

import asyncpg
import requests
from urllib.parse import quote

from config.settings import DATABASE_URL, MOLIT_API_KEY

MOLIT_API_URL = (
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
)

# 주요 지역 (법정동 코드 앞 5자리)
REGIONS = {
    "11110": "서울 종로구",
    "11140": "서울 중구",
    "11170": "서울 용산구",
    "11200": "서울 성동구",
    "11215": "서울 광진구",
    "11230": "서울 동대문구",
    "11260": "서울 중랑구",
    "11290": "서울 성북구",
    "11305": "서울 강북구",
    "11320": "서울 도봉구",
    "11350": "서울 노원구",
    "11380": "서울 은평구",
    "11410": "서울 서대문구",
    "11440": "서울 마포구",
    "11470": "서울 양천구",
    "11500": "서울 강서구",
    "11530": "서울 구로구",
    "11545": "서울 금천구",
    "11560": "서울 영등포구",
    "11590": "서울 동작구",
    "11620": "서울 관악구",
    "11650": "서울 서초구",
    "11680": "서울 강남구",
    "11710": "서울 송파구",
    "11740": "서울 강동구",
    "26000": "부산",
    "27000": "대구",
    "28000": "인천",
    "29000": "광주",
    "30000": "대전",
    "31000": "울산",
    "36000": "세종",
    "41000": "경기",
}


def _fetch_month(region_code: str, ym: str) -> list[dict]:
    if not MOLIT_API_KEY:
        return []
    # serviceKey를 URL에 직접 삽입 (requests param 사용 시 이중 인코딩 발생)
    encoded_key = quote(MOLIT_API_KEY, safe="")
    dealYm = ym.replace("-", "")
    url = (
        f"{MOLIT_API_URL}?serviceKey={encoded_key}"
        f"&pageNo=1&numOfRows=1000&LAWD_CD={region_code}&DEAL_YMD={dealYm}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        result = []
        for item in items:
            def t(tag, _item=item):
                el = _item.find(tag)
                return (el.text or "").strip() if el is not None else ""
            try:
                price = int(t("dealAmount").replace(",", ""))
                area  = float(t("excluUseAr"))
                price_per_sqm = (price / area) if area > 0 else None
            except (ValueError, ZeroDivisionError):
                price_per_sqm = None
            result.append({"price_per_sqm": price_per_sqm})
        return result
    except Exception as e:
        print(f"    [오류] {region_code}/{ym}: {e}")
        return []


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


async def load_realestate(start: str = "2015-01"):
    end = date.today().strftime("%Y-%m")
    print(f"국토교통부 실거래가 수집: {start} ~ {end}")

    if not MOLIT_API_KEY:
        print("  [건너뜀] MOLIT_API_KEY 미설정")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    total = 0

    yms = _ym_range(start, end)
    for i, ym in enumerate(yms):
        print(f"  [{i+1}/{len(yms)}] {ym} 수집 중...", end="\r")
        for region_code, region_name in REGIONS.items():
            trades = _fetch_month(region_code, ym)
            if not trades:
                continue
            prices = [t["price_per_sqm"] for t in trades if t["price_per_sqm"]]
            avg    = sum(prices) / len(prices) if prices else None
            count  = len(trades)

            await conn.execute("""
                INSERT INTO realestate_monthly
                    (ym, region_code, region_name, avg_price_10k, total_count)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (ym, region_code) DO UPDATE SET
                    avg_price_10k = COALESCE(EXCLUDED.avg_price_10k, realestate_monthly.avg_price_10k),
                    total_count   = COALESCE(EXCLUDED.total_count,   realestate_monthly.total_count),
                    updated_at    = NOW()
            """, ym, region_code, region_name, avg, count)
            total += 1
            time.sleep(0.1)  # API rate limit

    await conn.close()
    print(f"국토교통부 실거래가 적재 완료: {total}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01")
    args = parser.parse_args()
    asyncio.run(load_realestate(args.start))
