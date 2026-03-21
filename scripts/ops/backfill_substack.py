"""Substack 아카이브 백필 — archive API + 개별 포스트 스크래핑.

Usage:
    python scripts/ops/backfill_substack.py --source arthur_hayes --dry-run
    python scripts/ops/backfill_substack.py --source arthur_hayes
    python scripts/ops/backfill_substack.py --source glassnode_insights
"""

import argparse
import asyncio
import logging
import re
import time

import asyncpg
import requests
from dotenv import load_dotenv

load_dotenv()

import os

from src.collect.source_protocol import CollectedPost
from src.embed import get_embedder
from src.extract.realtime import extract_and_save

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; insight-verify/1.0)",
}

# Substack archive API: returns post metadata (no body)
# Paginate with offset, ~12 per page
_ARCHIVE_API = "{base_url}/api/v1/archive?sort=new&limit=12&offset={offset}"


def fetch_archive(base_url: str) -> list[dict]:
    """Substack archive API에서 전체 포스트 메타 수집."""
    all_posts = []
    offset = 0
    while True:
        url = _ARCHIVE_API.format(base_url=base_url, offset=offset)
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        if resp.status_code != 200:
            log.warning(f"Archive API HTTP {resp.status_code} at offset={offset}")
            break
        posts = resp.json()
        if not posts:
            break
        all_posts.extend(posts)
        log.info(f"  archive offset={offset}: {len(posts)}건")
        offset += len(posts)
        time.sleep(1)
    return all_posts


def fetch_post_content(post_url: str) -> str:
    """개별 포스트 페이지에서 본문 추출."""
    resp = requests.get(post_url, headers=_HEADERS, timeout=15)
    if resp.status_code != 200:
        return ""

    html = resp.text

    # Substack 본문은 <div class="body markup"> ... </div> 안에 있음
    # 또는 class="available-content" 안에
    for pattern in [
        r'<div[^>]*class="[^"]*available-content[^"]*"[^>]*>(.*?)</div>\s*<div[^>]*class="[^"]*subscription',
        r'<div[^>]*class="[^"]*body markup[^"]*"[^>]*>(.*?)</div>\s*(?:<div|<footer)',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            return _strip_html(m.group(1))

    # fallback: 모든 <p> 태그 내용 추출
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
    if paragraphs:
        text = " ".join(_strip_html(p) for p in paragraphs)
        # 최소 500자는 되어야 유효한 본문
        if len(text) > 500:
            return text

    return ""


def _strip_html(text: str) -> str:
    """HTML 태그 제거."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def backfill(source_name: str, dry_run: bool = False):
    """Substack 아카이브 백필."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    try:
        # 소스 정보 조회
        source = await conn.fetchrow(
            "SELECT id, platform, config FROM sources WHERE name = $1", source_name
        )
        if not source:
            log.error(f"Source '{source_name}' not found in DB")
            return

        import json
        raw_config = source["config"]
        if isinstance(raw_config, dict):
            config = raw_config
        elif isinstance(raw_config, str):
            config = json.loads(raw_config)
        else:
            config = {}
        feed_url = config.get("feed_url", "")
        # feed_url에서 base_url 추출: https://xxx.substack.com/feed → https://xxx.substack.com
        base_url = feed_url.replace("/feed", "").rstrip("/")
        if not base_url:
            log.error(f"No feed_url in config for '{source_name}'")
            return

        platform = source["platform"] or "substack"

        # 기존 URL 조회
        existing = set()
        rows = await conn.fetch(
            "SELECT url FROM mer_posts WHERE source_id = $1", source["id"]
        )
        for r in rows:
            existing.add(r["url"])
        log.info(f"[{source_name}] 기존 {len(existing)}건, base_url={base_url}")

        # archive API에서 전체 포스트 메타 가져오기
        archive = fetch_archive(base_url)
        log.info(f"[{source_name}] 아카이브 총 {len(archive)}건")

        # 새 포스트 필터링
        new_posts = []
        for p in archive:
            slug = p.get("slug", "")
            post_url = f"{base_url}/p/{slug}"
            if post_url in existing:
                continue
            new_posts.append({
                "title": p.get("title", ""),
                "slug": slug,
                "url": post_url,
                "date": (p.get("post_date") or "")[:10],  # YYYY-MM-DD
            })

        log.info(f"[{source_name}] 신규 {len(new_posts)}건 (기존 제외)")

        if dry_run:
            for i, p in enumerate(new_posts):
                log.info(f"  [{i+1}] {p['date']} — {p['title']}")
            return

        # 임베더 초기화 (extraction에 필요)
        embedder = get_embedder()

        # 포스트별 스크래핑 + 저장 + 추출
        saved = 0
        for i, post_meta in enumerate(new_posts):
            log.info(f"[{i+1}/{len(new_posts)}] {post_meta['title'][:50]}...")

            content = fetch_post_content(post_meta["url"])
            if len(content) < 200:
                log.warning(f"  본문 부족 ({len(content)}자), 스킵")
                continue

            # mer_posts에 저장
            from datetime import date as _date
            pub_date = None
            if post_meta["date"]:
                try:
                    pub_date = _date.fromisoformat(post_meta["date"])
                except ValueError:
                    pass

            post_id = await conn.fetchval("""
                INSERT INTO mer_posts (log_no, title, date, url, content_text, source_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (log_no) DO NOTHING
                RETURNING id
            """, post_meta["url"], post_meta["title"], pub_date,
                post_meta["url"], content, source["id"])

            if not post_id:
                log.debug(f"  이미 존재, 스킵")
                continue

            # 인사이트 추출
            post_dict = {
                "log_no": post_meta["url"],
                "title": post_meta["title"],
                "date": post_meta["date"],
                "url": post_meta["url"],
                "content_text": content,
            }
            result = await extract_and_save(conn, embedder, post_dict, source_type=platform)
            saved += 1
            log.info(f"  → 인사이트 {result['count']}개 추출")

            time.sleep(2)  # rate limit

        log.info(f"[{source_name}] 백필 완료: {saved}건 저장")

    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Substack 아카이브 백필")
    parser.add_argument("--source", required=True, help="sources 테이블 name (e.g. arthur_hayes)")
    parser.add_argument("--dry-run", action="store_true", help="스크래핑 없이 목록만 출력")
    args = parser.parse_args()

    asyncio.run(backfill(args.source, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
