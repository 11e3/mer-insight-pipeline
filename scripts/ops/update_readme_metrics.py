"""README 지표 자동 업데이트.

DB에서 최신 지표를 쿼리하여 README.md / README_EN.md의 지표 테이블을 갱신합니다.
daily-pipeline.yml 끝에서 실행됩니다.
"""

import os
import re
import sys


def fetch_metrics(dsn: str) -> dict:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct,
                    COUNT(*) FILTER (WHERE is_correct = FALSE) AS incorrect,
                    COUNT(*) FILTER (WHERE is_correct IS NULL AND is_verifiable = true) AS verifiable_pending,
                    COUNT(*) FILTER (WHERE is_correct IS NULL AND is_verifiable = false) AS unverifiable,
                    COUNT(*) FILTER (WHERE is_correct IS NULL AND expected_date > CURRENT_DATE) AS future
                FROM mer_predictions
            """)
            pred = dict(cur.fetchone())

            cur.execute("SELECT COUNT(*) AS cnt FROM news_headlines")
            headlines = cur.fetchone()["cnt"]

            cur.execute("SELECT COUNT(*) AS cnt FROM mer_predictions WHERE is_correct IS NOT NULL")
            verified = cur.fetchone()["cnt"]

    finally:
        conn.close()

    accuracy = pred["correct"] / verified * 100 if verified else 0
    return {
        "total": pred["total"],
        "correct": pred["correct"],
        "incorrect": pred["incorrect"],
        "verified": verified,
        "accuracy": round(accuracy, 1),
        "verifiable_pending": pred["verifiable_pending"],
        "unverifiable": pred["unverifiable"],
        "future": pred["future"],
        "headlines": headlines,
    }


def update_readme(path: str, m: dict, lang: str = "ko") -> bool:
    """README 파일의 지표 테이블을 업데이트. 변경 시 True 반환."""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    original = content

    # 지표 테이블 (상단)
    if lang == "ko":
        content = re.sub(
            r"(\| 추적 중인 예측 \| ).*?( \|)",
            rf"\g<1>{m['total']:,}건\2",
            content,
        )
        content = re.sub(
            r"(\| 자동 검증 완료 \| ).*?( \|)",
            rf"\g<1>{m['verified']:,}건 (적중률 {m['accuracy']}%)\2",
            content,
        )
        content = re.sub(
            r"(\| 뉴스 헤드라인 DB \| ).*?( \|)",
            rf"\g<1>{m['headlines']:,}건\2",
            content,
        )
    else:
        content = re.sub(
            r"(\| Predictions tracked \| ).*?( \|)",
            rf"\g<1>{m['total']:,}\2",
            content,
        )
        content = re.sub(
            r"(\| Auto-verified \| ).*?( \|)",
            rf"\g<1>{m['verified']:,} ({m['accuracy']}% accuracy)\2",
            content,
        )
        content = re.sub(
            r"(\| News headline DB \| ).*?( \|)",
            rf"\g<1>{m['headlines']:,}\2",
            content,
        )

    # 현재 현황 테이블
    content = re.sub(r"(\| CORRECT \| ).*?( \|)", rf"\g<1>{m['correct']:,}\2", content)
    content = re.sub(r"(\| INCORRECT \| ).*?( \|)", rf"\g<1>{m['incorrect']:,}\2", content)

    if lang == "ko":
        content = re.sub(
            r"(\| 검증 가능 PENDING \| ).*?( \|)",
            rf"\g<1>{m['verifiable_pending']:,}\2",
            content,
        )
        content = re.sub(
            r"(\| 검증 불가 \(모호/조건부\) \| ).*?( \|)",
            rf"\g<1>{m['unverifiable']:,}\2",
            content,
        )
        content = re.sub(
            r"(\| 미래 대기 \| ).*?( \|)",
            rf"\g<1>{m['future']:,}\2",
            content,
        )
    else:
        content = re.sub(
            r"(\| Verifiable PENDING \| ).*?( \|)",
            rf"\g<1>{m['verifiable_pending']:,}\2",
            content,
        )
        content = re.sub(
            r"(\| Unverifiable \(vague/conditional\) \| ).*?( \|)",
            rf"\g<1>{m['unverifiable']:,}\2",
            content,
        )
        content = re.sub(
            r"(\| Future \(awaiting expected_date\) \| ).*?( \|)",
            rf"\g<1>{m['future']:,}\2",
            content,
        )

    # Mermaid diagram 내 headlines 수
    content = re.sub(
        r"(news_headlines )\d+K",
        rf"\g<1>{m['headlines'] // 1000}K",
        content,
    )

    # 헤드라인 수치 (검증 방식 테이블)
    if lang == "ko":
        content = re.sub(
            r"(\| 헤드라인 \| )[\d,]+건",
            rf"\g<1>{m['headlines']:,}건",
            content,
        )
    else:
        content = re.sub(
            r"(\| Headlines \| )[\d,]+",
            rf"\g<1>{m['headlines']:,}",
            content,
        )

    if content != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    return False


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set, skipping README metrics update")
        sys.exit(0)

    m = fetch_metrics(dsn)
    print(f"Fetched metrics: {m['total']} predictions, {m['verified']} verified, "
          f"{m['accuracy']}% accuracy, {m['headlines']} headlines")

    ko_changed = update_readme("README.md", m, lang="ko")
    en_changed = update_readme("README_EN.md", m, lang="en")

    if ko_changed or en_changed:
        print("README metrics updated")
    else:
        print("No changes needed")


if __name__ == "__main__":
    main()
