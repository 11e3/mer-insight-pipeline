"""
메르 블로그 신규 글 감지.
네이버 RSS를 우선 사용, 실패 시 PostTitleListAsync API 폴백.
"""

import re
import time
import asyncpg
import requests
from xml.etree import ElementTree as ET

from config.settings import BLOG_ID, BLOG_RSS


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


class MerMonitor:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def check_new(self) -> list[dict]:
        """DB에 없는 신규 글만 반환 (본문 포함)."""
        log_nos = self._get_recent_log_nos()
        if not log_nos:
            return []

        # DB에 이미 있는 log_no 제외
        existing = {
            r["log_no"] for r in
            await self.conn.fetch(
                "SELECT log_no FROM mer_posts WHERE log_no = ANY($1)",
                log_nos
            )
        }

        new_log_nos = [n for n in dict.fromkeys(log_nos) if n not in existing]
        if not new_log_nos:
            return []

        posts = []
        for log_no in new_log_nos:
            post = self._scrape_post(log_no)
            if post:
                posts.append(post)
                await self._save_post(post)
                time.sleep(0.5)

        return posts

    def _get_recent_log_nos(self) -> list[str]:
        """RSS 또는 API로 최근 log_no 목록 (최대 30개)."""
        # RSS 시도
        try:
            resp = requests.get(BLOG_RSS, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                log_nos = []
                for item in root.findall(".//item/link"):
                    m = re.search(r"/(\d{10,})$", (item.text or ""))
                    if m:
                        log_nos.append(m.group(1))
                if log_nos:
                    return log_nos
        except Exception:
            pass

        # 폴백: PostTitleListAsync API
        try:
            url = (
                f"https://blog.naver.com/PostTitleListAsync.naver"
                f"?blogId={BLOG_ID}&viewdate=&currentPage=1"
                f"&categoryNo=&parentCategoryNo=&countPerPage=30"
            )
            resp = requests.get(url, headers=HEADERS, timeout=10)
            return re.findall(r'logNo[\"=:]+(\d{10,})', resp.text)
        except Exception:
            return []

    def _scrape_post(self, log_no: str) -> dict | None:
        """모바일 URL에서 포스트 본문 스크래핑."""
        from bs4 import BeautifulSoup
        from src.ingest.date_parser import parse_mer_date

        url = f"https://m.blog.naver.com/{BLOG_ID}/{log_no}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        title_tag = (
            soup.select_one(".se-title-text span")
            or soup.select_one(".se-title-text")
            or soup.select_one("title")
        )
        title = title_tag.get_text(strip=True) if title_tag else f"post_{log_no}"
        title = re.sub(r"\s*[:|]\s*네이버 블로그.*$", "", title).strip()

        date_tag = soup.select_one(".blog_date")
        date_val = parse_mer_date(date_tag.get_text(strip=True) if date_tag else "")

        content_tag = soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
        content_text = ""
        if content_tag:
            for t in content_tag.select("script, style"):
                t.decompose()
            content_text = re.sub(r'\n{3,}', '\n\n', content_tag.get_text(separator="\n", strip=True))

        return {
            "log_no": log_no,
            "title": title,
            "date": date_val,
            "url": f"https://blog.naver.com/{BLOG_ID}/{log_no}",
            "content_text": content_text,
        }

    async def _save_post(self, post: dict):
        await self.conn.execute("""
            INSERT INTO mer_posts (log_no, title, date, url, content_text, word_count)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (log_no) DO NOTHING
        """,
            post["log_no"], post["title"], post.get("date"),
            post["url"], post["content_text"],
            len(post["content_text"].replace(" ", "").replace("\n", "")),
        )
