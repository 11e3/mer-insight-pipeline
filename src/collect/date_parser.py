"""
메르 블로그 날짜 파싱
  절대시간: "2024. 9. 4. 0:10"  /  "2024.09.04"  /  "2024-09-04"
  상대시간: "9시간 전"  /  "3일 전"  /  "1주 전"
"""

import re
from datetime import datetime, timedelta, date


def parse_mer_date(date_str: str, scraped_at: datetime | None = None) -> date | None:
    """
    날짜 문자열 → date 반환.
    상대시간은 scraped_at (스크래핑 시점) 기준으로 계산.
    scraped_at 미전달 시 datetime.now() 사용.
    """
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()
    base = scraped_at or datetime.now()

    # 상대시간
    relative = [
        (r"(\d+)분\s*전",  lambda m, b: b - timedelta(minutes=int(m.group(1)))),
        (r"(\d+)시간\s*전", lambda m, b: b - timedelta(hours=int(m.group(1)))),
        (r"(\d+)일\s*전",  lambda m, b: b - timedelta(days=int(m.group(1)))),
        (r"(\d+)주\s*전",  lambda m, b: b - timedelta(weeks=int(m.group(1)))),
        (r"(\d+)달\s*전",  lambda m, b: b - timedelta(days=int(m.group(1)) * 30)),
    ]
    for pattern, fn in relative:
        m = re.search(pattern, date_str)
        if m:
            return fn(m, base).date()

    # 절대시간 패턴 (긴 것 먼저)
    absolute = [
        (r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{1,2}):(\d{2})",
         lambda g: date(int(g[0]), int(g[1]), int(g[2]))),   # 2024. 9. 4. 0:10
        (r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})",
         lambda g: date(int(g[0]), int(g[1]), int(g[2]))),   # 2024. 9. 4.
        (r"(\d{4})-(\d{1,2})-(\d{1,2})",
         lambda g: date(int(g[0]), int(g[1]), int(g[2]))),   # 2024-09-04
        (r"(\d{4})/(\d{1,2})/(\d{1,2})",
         lambda g: date(int(g[0]), int(g[1]), int(g[2]))),   # 2024/09/04
    ]
    for pattern, fn in absolute:
        m = re.search(pattern, date_str)
        if m:
            try:
                return fn(m.groups())
            except ValueError:
                continue

    return None
