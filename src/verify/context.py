"""예측 검증용 컨텍스트 수집: 매크로, 주가, DART, 뉴스."""

import asyncio
import logging
from datetime import date
from xml.etree import ElementTree as ET

import asyncpg
import requests

from src.verify.prompt import KR_STOCKS, HEADERS

log = logging.getLogger(__name__)


async def build_context(conn: asyncpg.Connection, since: date) -> str:
    """검증에 필요한 컨텍스트 문자열 조립."""
    until = date.today()
    parts: list[str] = []

    # 1. 월별 macro 요약
    macro_rows = await conn.fetch("""
        SELECT
            to_char(date, 'YYYY-MM') AS ym,
            ROUND(AVG(kospi)::numeric, 0)          AS kospi_avg,
            ROUND(MAX(kospi)::numeric, 0)          AS kospi_max,
            ROUND(MIN(kospi)::numeric, 0)          AS kospi_min,
            ROUND(AVG(usd_krw)::numeric, 0)        AS krw_avg,
            ROUND(MAX(usd_krw)::numeric, 0)        AS krw_max,
            ROUND(AVG(wti)::numeric, 1)            AS wti_avg,
            ROUND(MAX(wti)::numeric, 1)            AS wti_max,
            ROUND(AVG(us_10y)::numeric, 2)         AS us10y_avg,
            ROUND(AVG(fed_funds_rate)::numeric, 2) AS fed_avg,
            ROUND(AVG(vix)::numeric, 1)            AS vix_avg,
            ROUND(AVG(btc_usd)::numeric, 0)        AS btc_avg
        FROM macro_daily
        WHERE date BETWEEN $1 AND $2
        GROUP BY ym ORDER BY ym
    """, since, until)

    if macro_rows:
        lines = [f"[월별 매크로 (고/저/평균): {since} ~ {until}]"]
        for r in macro_rows:
            lines.append(
                f"{r['ym']}: KOSPI={r['kospi_avg']}(고{r['kospi_max']}/저{r['kospi_min']}) "
                f"USD/KRW={r['krw_avg']}(고{r['krw_max']}) "
                f"WTI={r['wti_avg']}(고{r['wti_max']}) "
                f"US10Y={r['us10y_avg']}% Fed={r['fed_avg']}% VIX={r['vix_avg']} BTC={r['btc_avg']}"
            )
        parts.append("\n".join(lines))

    # 1-1. 최근 30일 일간 매크로
    recent_rows = await conn.fetch("""
        SELECT date, kospi, usd_krw, wti, us_10y, vix, btc_usd
        FROM macro_daily
        WHERE date BETWEEN CURRENT_DATE - INTERVAL '30 days' AND $1
        ORDER BY date
    """, until)
    if recent_rows:
        lines = ["[최근 30일 일간 매크로]"]
        for r in recent_rows:
            lines.append(
                f"{r['date']}: KOSPI={r['kospi'] and int(r['kospi'])} "
                f"KRW={r['usd_krw'] and int(r['usd_krw'])} "
                f"WTI={r['wti']} US10Y={r['us_10y']} VIX={r['vix']} BTC={r['btc_usd'] and int(r['btc_usd'])}"
            )
        parts.append("\n".join(lines))

    # 2. 주요 한국 주식 월별 종가
    stock_ctx = await asyncio.get_event_loop().run_in_executor(
        None, fetch_stock_context, since, until
    )
    if stock_ctx:
        parts.append(stock_ctx)

    # 3. DART 공시 + 뉴스 (기업 이벤트 검증용)
    event_rows = await conn.fetch("""
        SELECT event_type, title, content, event_date
        FROM events
        WHERE event_type IN ('dart', 'news')
          AND event_date::date BETWEEN $1 AND $2
        ORDER BY event_date DESC
        LIMIT 20
    """, since, until)

    if event_rows:
        lines = [f"[DART 공시 · 뉴스: {since} ~ {until}]"]
        for r in event_rows:
            tag = "DART" if r["event_type"] == "dart" else "뉴스"
            lines.append(
                f"[{r['event_date'].strftime('%Y-%m-%d')} {tag}] {r['title']}\n"
                f"{(r['content'] or '')[:150]}"
            )
        parts.append("\n\n".join(lines))

    return "\n\n".join(parts) if parts else "(컨텍스트 없음)"


def fetch_stock_context(since: date, until: date) -> str:
    """네이버 금융에서 주요 종목 주가 수집."""
    days = (until - since).days
    count = min(days + 30, 1800)

    lines = ["[주요 한국 주식 주가]"]
    fetched = 0

    for name, code in KR_STOCKS.items():
        try:
            url = (
                f"https://fchart.stock.naver.com/sise.nhn"
                f"?symbol={code}&timeframe=day&count={count}&requestType=0"
            )
            resp = requests.get(url, headers=HEADERS, timeout=10)
            root = ET.fromstring(resp.content.decode("euc-kr", errors="replace"))

            by_month: dict[str, list[int]] = {}
            latest_close: str | None = None

            for item in root.findall(".//item"):
                raw = item.get("data", "")
                parts = raw.split("|")
                if len(parts) < 5:
                    continue
                dt, close = parts[0], parts[4]
                if not dt or not close:
                    continue
                try:
                    d = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
                    c = int(close)
                except ValueError:
                    continue
                if d < since or d > until:
                    continue

                ym = dt[:6]
                by_month.setdefault(ym, []).append(c)
                latest_close = f"{d}={c:,}"

            parts_line: list[str] = []

            if by_month:
                monthly = " ".join(
                    f"{ym[:4]}-{ym[4:]}(H={max(v):,}/L={min(v):,}/C={v[-1]:,})"
                    for ym, v in sorted(by_month.items())
                )
                parts_line.append(monthly)

            if latest_close:
                parts_line.append(f"최신={latest_close}")

            if parts_line:
                lines.append(f"{name}: " + " | ".join(parts_line))
                fetched += 1

        except Exception as e:
            log.debug(f"주가 수집 실패 ({name}): {e}")

    return "\n".join(lines) if fetched else ""
