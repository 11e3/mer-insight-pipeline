"""
스크래핑된 JSON 파일들을 PostgreSQL posts 테이블에 적재.

Usage:
    python -m src.collect.posts
"""

import os
import json
import asyncio
from tqdm import tqdm

from src.config.settings import RAW_JSON_DIR
from src.collect.date_parser import parse_mer_date


async def load_posts(json_dir: str = RAW_JSON_DIR):
    from src.db import connect

    async with connect() as conn:
        json_files = [f for f in os.listdir(json_dir) if f.endswith(".json")]
        print(f"JSON 파일 {len(json_files)}개 발견 → PostgreSQL 적재 시작\n")

        inserted, skipped, errors = 0, 0, 0

        for fname in tqdm(json_files, desc="적재 중"):
            path = os.path.join(json_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    post = json.load(f)
            except Exception as e:
                print(f"\n[오류] {fname}: {e}")
                errors += 1
                continue

            date_val = parse_mer_date(post.get("date", ""))
            word_count = len(post.get("content_text", "").replace(" ", "").replace("\n", ""))

            try:
                await conn.execute("""
                    INSERT INTO posts
                        (log_no, title, date, url, content_text, image_urls, tags, word_count)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (log_no) DO UPDATE SET
                        title        = EXCLUDED.title,
                        date         = EXCLUDED.date,
                        content_text = EXCLUDED.content_text,
                        image_urls   = EXCLUDED.image_urls,
                        tags         = EXCLUDED.tags,
                        word_count   = EXCLUDED.word_count
                """,
                    post["log_no"],
                    post.get("title", ""),
                    date_val,
                    post.get("url", ""),
                    post.get("content_text", ""),
                    post.get("images", []),
                    post.get("tags", []),
                    word_count,
                )
                inserted += 1
            except Exception as e:
                print(f"\n[DB 오류] {post.get('log_no')}: {e}")
                errors += 1

        print(f"\n완료: 삽입/업데이트 {inserted}개 | 스킵 {skipped}개 | 오류 {errors}개")


if __name__ == "__main__":
    asyncio.run(load_posts())
