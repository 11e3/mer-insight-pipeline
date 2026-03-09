"""
Claude Batch API로 2,193개 포스트에서 인사이트 추출.

Usage:
    python -m src.extract.batch_api create            # 배치 생성
    python -m src.extract.batch_api status <id>       # 진행 상황 확인
    python -m src.extract.batch_api download <id>     # 결과 다운로드
    python -m src.extract.batch_api retokenize <results.jsonl>  # max_tokens 짤린 포스트 재배치
"""

import os
import json
import asyncio
import asyncpg
import anthropic

from config.settings import DATABASE_URL, ANTHROPIC_API_KEY, MODEL_SONNET, MODEL_HAIKU
from config.prompts import INSIGHT_SYSTEM_PROMPT, INSIGHT_USER_TEMPLATE

BATCH_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "batches")
os.makedirs(BATCH_DIR, exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ─── 배치 요청 생성 ──────────────────────────────────────────────────────────

def make_request(post: dict, model: str, max_tokens: int = 4096) -> dict:
    return {
        "custom_id": f"mer-{post['log_no']}",
        "params": {
            "model": model,
            "max_tokens": max_tokens,
            "system": INSIGHT_SYSTEM_PROMPT,
            "messages": [{
                "role": "user",
                "content": INSIGHT_USER_TEMPLATE.format(
                    title=post["title"],
                    date=post.get("date") or "",
                    url=post.get("url") or "",
                    content_text=(post.get("content_text") or "")[:8000],  # 토큰 절감
                ),
            }],
        },
    }


async def fetch_unprocessed_posts(conn: asyncpg.Connection) -> list[dict]:
    """아직 인사이트가 없는 포스트 목록 반환."""
    rows = await conn.fetch("""
        SELECT p.log_no, p.title, p.date::text, p.url, p.content_text
        FROM mer_posts p
        WHERE NOT EXISTS (
            SELECT 1 FROM mer_insights i WHERE i.post_id = p.id
        )
        ORDER BY p.date DESC NULLS LAST
    """)
    return [dict(r) for r in rows]


async def create_batch(model: str = MODEL_SONNET, limit: int | None = None) -> str:
    """배치 생성 → batch_id 반환."""
    conn = await asyncpg.connect(DATABASE_URL)
    posts = await fetch_unprocessed_posts(conn)
    await conn.close()

    if limit:
        posts = posts[:limit]

    print(f"배치 생성: {len(posts)}개 포스트 / 모델: {model}")
    requests = [make_request(p, model) for p in posts]

    # 배치 API는 요청당 최대 10,000개
    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id

    # batch_id 저장
    id_file = os.path.join(BATCH_DIR, "batch_ids.jsonl")
    with open(id_file, "a") as f:
        f.write(json.dumps({"batch_id": batch_id, "model": model, "count": len(posts)}) + "\n")

    print(f"배치 생성 완료: {batch_id}")
    print(f"상태 확인: python -m src.extract.batch_api status {batch_id}")
    return batch_id


async def create_retokenize_batch(results_jsonl: str, model: str = MODEL_SONNET) -> str:
    """이전 배치에서 max_tokens로 짤린 포스트만 골라 max_tokens=8192로 재배치."""
    # 1. stop_reason="max_tokens"인 log_no 수집
    truncated_log_nos: set[str] = set()
    with open(results_jsonl, encoding="utf-8") as f:
        for line in f:
            try:
                result = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            result_obj = result.get("result", {})
            if result_obj.get("type") != "succeeded":
                continue
            stop_reason = (
                result_obj.get("message", {}).get("stop_reason", "")
            )
            if stop_reason == "max_tokens":
                log_no = result.get("custom_id", "").replace("mer-", "")
                if log_no:
                    truncated_log_nos.add(log_no)

    print(f"max_tokens 짤린 포스트: {len(truncated_log_nos)}개")
    if not truncated_log_nos:
        print("재처리 대상 없음.")
        return ""

    # 2. DB에서 해당 포스트 조회
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("""
        SELECT log_no, title, date::text, url, content_text
        FROM mer_posts
        WHERE log_no = ANY($1::text[])
    """, list(truncated_log_nos))
    await conn.close()

    posts = [dict(r) for r in rows]
    print(f"DB에서 조회된 포스트: {len(posts)}개 / 모델: {model} / max_tokens: 8192")

    requests = [make_request(p, model, max_tokens=8192) for p in posts]

    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id

    id_file = os.path.join(BATCH_DIR, "batch_ids.jsonl")
    with open(id_file, "a") as f:
        f.write(json.dumps({
            "batch_id": batch_id,
            "model": model,
            "count": len(posts),
            "note": "retokenize_8192",
        }) + "\n")

    print(f"재배치 생성 완료: {batch_id}")
    print(f"상태 확인: python -m src.extract.batch_api status {batch_id}")
    return batch_id


def check_status(batch_id: str):
    """배치 진행 상황 출력."""
    batch = client.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    total = counts.processing + counts.succeeded + counts.errored + counts.expired + counts.canceled
    print(f"배치 {batch_id}")
    print(f"  상태: {batch.processing_status}")
    print(f"  처리 중: {counts.processing} / 완료: {counts.succeeded} / 오류: {counts.errored} / 전체: {total}")
    return batch.processing_status == "ended"


def download_results(batch_id: str) -> str:
    """배치 결과 다운로드 → JSONL 파일 경로 반환."""
    out_path = os.path.join(BATCH_DIR, f"{batch_id}_results.jsonl")

    with open(out_path, "w", encoding="utf-8") as f:
        for result in client.messages.batches.results(batch_id):
            f.write(result.model_dump_json() + "\n")

    print(f"다운로드 완료: {out_path}")
    return out_path


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "create":
        model = MODEL_HAIKU if "--haiku" in sys.argv else MODEL_SONNET
        limit = None
        if "--limit" in sys.argv:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        asyncio.run(create_batch(model=model, limit=limit))

    elif cmd == "status":
        batch_id = sys.argv[2]
        check_status(batch_id)

    elif cmd == "download":
        batch_id = sys.argv[2]
        download_results(batch_id)

    elif cmd == "retokenize":
        if len(sys.argv) < 3:
            print("Usage: retokenize <results.jsonl> [--haiku]")
            sys.exit(1)
        results_path = sys.argv[2]
        model = MODEL_HAIKU if "--haiku" in sys.argv else MODEL_SONNET
        asyncio.run(create_retokenize_batch(results_path, model=model))

    else:
        print("Usage:")
        print("  create [--haiku] [--limit N]")
        print("  status  <batch_id>")
        print("  download <batch_id>")
        print("  retokenize <results.jsonl> [--haiku]")
