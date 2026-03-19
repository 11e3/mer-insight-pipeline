"""
Naver Blog Scraper - blog.naver.com/ranto28
Usage:
    pip install requests beautifulsoup4 lxml
    python naver_blog_scraper.py
"""

import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup

BLOG_ID = "ranto28"
OUTPUT_DIR = "scraped_posts"
DELAY = 0.8  # seconds between requests
COUNT_PER_PAGE = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Referer": "https://blog.naver.com/",
}

session = requests.Session()
session.headers.update(HEADERS)


# ─── 1. 포스트 목록 수집 ─────────────────────────────────────────────────────

def get_post_ids() -> list[str]:
    """PostTitleListAsync API로 모든 포스트 logNo 수집."""
    post_ids = []
    page = 1

    while True:
        url = (
            f"https://blog.naver.com/PostTitleListAsync.naver"
            f"?blogId={BLOG_ID}&viewdate=&currentPage={page}"
            f"&categoryNo=&parentCategoryNo=&countPerPage={COUNT_PER_PAGE}"
        )
        try:
            resp = session.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [오류] 목록 페이지 {page}: {e}")
            break

        found = re.findall(r'logNo[\"=:]+(\d{10,})', resp.text)
        new_ids = [lid for lid in found if lid not in post_ids]

        if not new_ids:
            break

        post_ids.extend(new_ids)

        # 전체 수 파악 (첫 페이지에서)
        if page == 1:
            m = re.search(r'totalCount[^\d]*(\d+)', resp.text, re.I)
            if m:
                total = int(m.group(1))
                total_pages = (total + COUNT_PER_PAGE - 1) // COUNT_PER_PAGE
                print(f"  전체 포스트: {total}개 ({total_pages}페이지)")

        print(f"  페이지 {page}: {len(new_ids)}개 수집 (누적 {len(post_ids)}개)")
        page += 1
        time.sleep(DELAY)

    return post_ids


# ─── 2. 포스트 본문 스크래핑 ─────────────────────────────────────────────────

def scrape_post(log_no: str) -> dict | None:
    """모바일 URL에서 포스트 본문을 파싱합니다."""
    url = f"https://m.blog.naver.com/{BLOG_ID}/{log_no}"

    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[오류] {log_no}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # 제목
    title_tag = (
        soup.select_one(".se-title-text span")
        or soup.select_one(".se-title-text")
        or soup.select_one(".tit_h3")
        or soup.select_one("h3.tit_view")
        or soup.select_one(".pcol1")
        or soup.select_one("title")
    )
    title = title_tag.get_text(strip=True) if title_tag else f"post_{log_no}"
    # 블로그명 suffix 제거
    title = re.sub(r"\s*[:|]\s*네이버 블로그.*$", "", title).strip()

    # 날짜
    date_tag = (
        soup.select_one(".blog_date")        # 모바일 버전
        or soup.select_one(".se_publishDate") # PC iframe
        or soup.select_one(".se-date")
        or soup.select_one("span.date")
    )
    date_str = date_tag.get_text(strip=True) if date_tag else ""

    # 본문
    content_tag = (
        soup.select_one(".se-main-container")
        or soup.select_one("#postViewArea")
        or soup.select_one(".post_ct")
        or soup.select_one(".view")
    )

    if content_tag:
        for tag in content_tag.select("script, style, .se-oglink-info, .se-module-comment"):
            tag.decompose()
        content_text = content_tag.get_text(separator="\n", strip=True)
        # 연속된 빈 줄 정리
        content_text = re.sub(r'\n{3,}', '\n\n', content_text)
    else:
        content_text = ""

    # 이미지 URL
    images = []
    for img in soup.select("img[src]"):
        src = img.get("src", "")
        if any(kw in src for kw in ["postfiles", "blogfiles", "pstatic"]):
            src = re.sub(r"\?.*$", "", src)
            if src not in images:
                images.append(src)

    # 태그
    tags = [t.get_text(strip=True) for t in soup.select(".se-hash-tag, .taglist a, .tag")]
    tags = [t for t in tags if t and not t.startswith("http")]

    return {
        "log_no": log_no,
        "title": title,
        "date": date_str,
        "url": f"https://blog.naver.com/{BLOG_ID}/{log_no}",
        "content_text": content_text,
        "images": images,
        "tags": tags,
    }


# ─── 3. 저장 ────────────────────────────────────────────────────────────────

def save_post(post: dict):
    """포스트를 txt와 json으로 저장."""
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", post["title"])[:80]
    base_name = f"{post['log_no']}_{safe_title}"

    # 텍스트
    txt_dir = os.path.join(OUTPUT_DIR, "txt")
    os.makedirs(txt_dir, exist_ok=True)
    with open(os.path.join(txt_dir, f"{base_name}.txt"), "w", encoding="utf-8") as f:
        f.write(f"제목: {post['title']}\n")
        f.write(f"날짜: {post['date']}\n")
        f.write(f"URL: {post['url']}\n")
        if post["tags"]:
            f.write(f"태그: {', '.join(post['tags'])}\n")
        f.write("\n" + "=" * 60 + "\n\n")
        f.write(post["content_text"])

    # JSON
    json_dir = os.path.join(OUTPUT_DIR, "json")
    os.makedirs(json_dir, exist_ok=True)
    with open(os.path.join(json_dir, f"{base_name}.json"), "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)


# ─── 메인 ───────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    done_file = os.path.join(OUTPUT_DIR, "done.txt")

    print(f"=== Naver Blog Scraper: blog.naver.com/{BLOG_ID} ===\n")

    # 이미 완료된 포스트 로드
    done_ids = set()
    if os.path.exists(done_file):
        with open(done_file, encoding="utf-8") as f:
            done_ids = {x.strip() for x in f if x.strip()}
    if done_ids:
        print(f"이미 완료된 포스트: {len(done_ids)}개 (건너뜀)\n")

    # 포스트 ID 수집
    print("[1단계] 포스트 목록 수집 중...")
    all_ids = get_post_ids()

    if not all_ids:
        print("포스트를 찾을 수 없습니다.")
        return

    remaining = [pid for pid in all_ids if pid not in done_ids]
    print(f"\n총 {len(all_ids)}개 포스트 / 남은 작업: {len(remaining)}개\n")

    # 스크래핑
    print("[2단계] 포스트 본문 스크래핑 중...\n")
    success, fail = 0, 0

    for i, log_no in enumerate(remaining, 1):
        post = scrape_post(log_no)

        if post:
            save_post(post)
            with open(done_file, "a", encoding="utf-8") as f:
                f.write(f"{log_no}\n")
            success += 1
            short_title = post["title"][:35]
            print(f"  [{i}/{len(remaining)}] OK  {log_no}  {short_title}")
        else:
            fail += 1
            print(f"  [{i}/{len(remaining)}] FAIL {log_no}")

        time.sleep(DELAY)

    # 요약
    print("\n=== 완료 ===")
    print(f"성공: {success}개 / 실패: {fail}개")
    print(f"저장 위치: {os.path.abspath(OUTPUT_DIR)}/")
    print("  txt/  - 읽기 좋은 텍스트 파일")
    print("  json/ - 전체 데이터 (이미지 URL 포함)")


if __name__ == "__main__":
    main()
