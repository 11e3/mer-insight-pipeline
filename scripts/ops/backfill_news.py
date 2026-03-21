"""뉴스 헤드라인 역사적 백필 — Google News RSS 월별 수집.

Usage:
    python scripts/ops/backfill_news.py --start 2022-01 --end 2025-12
    python scripts/ops/backfill_news.py --start 2024-06 --end 2024-06  # 단일 월
"""

import argparse
import asyncio
import logging
from datetime import datetime

import asyncpg
import feedparser
from dotenv import load_dotenv

load_dotenv()

import os
from src.collect.keyword_extractor import extract_keywords

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# 백필용 검색 쿼리 (한국어 + 영어)
BACKFILL_QUERIES = [
    # 영어 — 매크로
    ("en", "Federal Reserve interest rate"),
    ("en", "US Treasury 10 year yield"),
    ("en", "US inflation CPI"),
    ("en", "Bank of Japan interest rate yen"),
    ("en", "China economy GDP growth"),
    # 영어 — 자산
    ("en", "crude oil WTI Brent price"),
    ("en", "gold price XAU"),
    ("en", "silver price XAG"),
    ("en", "copper price LME"),
    ("en", "S&P 500 stock market"),
    # 영어 — 산업
    ("en", "South Korea KOSPI economy"),
    ("en", "semiconductor TSMC Samsung HBM"),
    ("en", "Tesla TSLA Elon Musk"),
    ("en", "defense Korea Hanwha KAI"),
    # 한국어 — 매크로
    ("ko", "한국은행 기준금리"),
    ("ko", "금리 인하 인상"),
    ("ko", "물가 인플레이션 CPI"),
    ("ko", "환율 달러 원화"),
    ("ko", "엔화 엔달러 일본은행"),
    ("ko", "국채 채권 금리"),
    # 한국어 — 자산
    ("ko", "코스피 주식시장"),
    ("ko", "부동산 아파트 매매"),
    ("ko", "금값 구리 원자재"),
    ("ko", "은값 은가격 은시세"),
    # 한국어 — 산업
    ("ko", "반도체 삼성전자 SK하이닉스"),
    ("ko", "파운드리 TSMC 삼성전자 3나노"),
    ("ko", "원유 천연가스 에너지"),
    ("ko", "조선 LNG선 수주"),
    ("ko", "방산 수출 한화 천궁"),
    ("ko", "저축은행 새마을금고 부실"),
    ("ko", "테슬라 일론머스크"),
    ("ko", "중국 GDP 경제성장"),
    # 크립토
    ("en", "Bitcoin BTC price"),
    ("en", "Ethereum ETH price"),
    ("en", "DeFi stablecoin USDT USDC"),
    ("en", "crypto market altcoin"),
    ("en", "on-chain whale exchange flow"),
    ("ko", "비트코인 BTC 암호화폐"),
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mer-insight-bot/1.0)",
}


def _build_url(query: str, lang: str, year: int, month: int) -> str:
    """Google News RSS URL with date range."""
    start = f"{year}-{month:02d}-01"
    # 다음 달 1일
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"

    encoded_query = query.replace(" ", "+")
    date_filter = f"+after:{start}+before:{end}"

    if lang == "ko":
        return f"https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q={encoded_query}{date_filter}"
    return f"https://news.google.com/rss/search?hl=en&gl=US&ceid=US:en&q={encoded_query}{date_filter}"


def _parse_feed(url: str) -> list[dict]:
    """RSS 파싱 → [{headline, url, published_at, source_name}]"""
    parsed = feedparser.parse(url, request_headers=_HEADERS)
    results = []
    for entry in parsed.entries:
        headline = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not headline or not link:
            continue

        pub = None
        for attr in ("published_parsed", "updated_parsed"):
            pp = getattr(entry, attr, None)
            if pp:
                pub = datetime(*pp[:6])
                break

        source_name = (
            entry.get("source", {}).get("title")
            or parsed.feed.get("title", "Google News")
        )

        results.append({
            "headline": headline,
            "source_url": link,
            "published_at": pub or datetime.utcnow(),
            "source_name": source_name,
        })
    return results


async def backfill(start_ym: str, end_ym: str):
    """월별 백필 실행."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    start_year, start_month = map(int, start_ym.split("-"))
    end_year, end_month = map(int, end_ym.split("-"))

    total_inserted = 0
    current_year, current_month = start_year, start_month

    while (current_year, current_month) <= (end_year, end_month):
        month_total = 0
        for lang, query in BACKFILL_QUERIES:
            url = _build_url(query, lang, current_year, current_month)

            try:
                articles = await asyncio.to_thread(_parse_feed, url)
            except Exception as e:
                log.warning(f"  피드 실패: {query} ({e})")
                await asyncio.sleep(2)
                continue

            for a in articles:
                keywords = extract_keywords(a["headline"], lang)
                try:
                    result = await conn.execute("""
                        INSERT INTO news_headlines
                            (headline, source_url, source_name, language,
                             published_at, keywords, feed_topic, is_backfill)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                        ON CONFLICT (md5(source_url)) DO NOTHING
                    """,
                        a["headline"], a["source_url"], a["source_name"], lang,
                        a["published_at"], keywords, query.split()[0],
                    )
                    if result == "INSERT 0 1":
                        month_total += 1
                except Exception:
                    pass

            await asyncio.sleep(2)  # Google rate limit

        total_inserted += month_total
        log.info(f"  {current_year}-{current_month:02d}: {month_total}건 삽입")

        # 다음 달
        if current_month == 12:
            current_year += 1
            current_month = 1
        else:
            current_month += 1

    await conn.close()
    log.info(f"백필 완료: 총 {total_inserted}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="뉴스 헤드라인 백필")
    parser.add_argument("--start", required=True, help="시작 월 (YYYY-MM)")
    parser.add_argument("--end", required=True, help="종료 월 (YYYY-MM)")
    args = parser.parse_args()
    asyncio.run(backfill(args.start, args.end))
