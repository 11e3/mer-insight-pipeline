"""
DART 공시 수집 (OpenAPI).
API 키 없이도 RSS로 최근 공시 수집 가능.
"""

import asyncpg
import requests
from xml.etree import ElementTree as ET
from datetime import datetime


# DART RSS (API 키 불필요)
DART_RSS_URL = "https://opendart.fss.or.kr/api/rssInfo.do?typeCode=I001"
DART_DETAIL_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp_no}"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# 주요 공시 유형만 필터링
IMPORTANT_REPORT_TYPES = {
    "사업보고서", "반기보고서", "분기보고서",
    "주요사항보고서", "증권신고서",
    "합병", "분할", "유상증자", "자기주식",
    "공정공시", "최대주주변경",
}


class DartCollector:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_recent(self) -> list[dict]:
        """최근 공시 목록 반환 (DB에 없는 것만)."""
        filings = self._fetch_rss()
        if not filings:
            return []

        # 이미 처리된 공시 제외
        new_filings = []
        for f in filings:
            exists = await self.conn.fetchval(
                "SELECT 1 FROM events WHERE source = $1 AND event_type = 'dart'",
                f["url"]
            )
            if not exists:
                new_filings.append(f)

        return new_filings

    def _fetch_rss(self) -> list[dict]:
        """DART RSS에서 공시 목록 수집."""
        try:
            resp = requests.get(DART_RSS_URL, headers=HEADERS, timeout=10)
            resp.encoding = "utf-8"
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"DART RSS 오류: {e}")
            return []

        filings = []
        for item in root.findall(".//item"):
            title = _text(item, "title")
            link  = _text(item, "link")
            pub   = _text(item, "pubDate")
            corp  = _text(item, "companyName") or ""
            rtype = _text(item, "reportName") or ""

            # 중요 공시만 필터 (전체 수집 원하면 이 조건 제거)
            if not any(k in (title + rtype) for k in IMPORTANT_REPORT_TYPES):
                continue

            try:
                pub_dt = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S")
            except Exception:
                pub_dt = datetime.now()

            filings.append({
                "title":   f"[{corp}] {title}",
                "content": f"기업: {corp}\n공시유형: {rtype}\nURL: {link}",
                "url":     link,
                "date":    pub_dt,
            })

        return filings


def _text(element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""
