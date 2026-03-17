"""기존 PENDING 예측의 expected_date를 텍스트에서 파싱하여 채운다."""
import asyncio
import os
import re
from datetime import date
from dotenv import load_dotenv
import asyncpg

load_dotenv()


def parse_expected_date(text: str, prediction_date: date | None) -> date | None:
    """예측 텍스트에서 예상 실현 시점을 추출한다."""
    if not text:
        return None

    # "2027년까지", "2030년", "2028년 말"
    m = re.search(r"(20[2-9]\d)년", text)
    if m:
        year = int(m.group(1))
        if year > 2026:
            # "말" → 12월, "상반기" → 6월, 기본 → 1월
            if "말" in text[m.end():m.end()+5]:
                return date(year, 12, 31)
            elif "상반기" in text[m.end():m.end()+10]:
                return date(year, 6, 30)
            elif "하반기" in text[m.end():m.end()+10]:
                return date(year, 12, 31)
            return date(year, 1, 1)

    # "향후 N년", "N년 내", "N년 이내"
    m = re.search(r"향후\s*(\d+)\s*년|(\d+)\s*년\s*(내|이내|안)", text)
    if m and prediction_date:
        n = int(m.group(1) or m.group(2))
        target_year = prediction_date.year + n
        if target_year > 2026:
            return date(target_year, prediction_date.month, 1)

    # "N개월 내", "향후 N개월"
    m = re.search(r"향후\s*(\d+)\s*개월|(\d+)\s*개월\s*(내|이내|안)", text)
    if m and prediction_date:
        months = int(m.group(1) or m.group(2))
        target_month = prediction_date.month + months
        target_year = prediction_date.year + (target_month - 1) // 12
        target_month = (target_month - 1) % 12 + 1
        if date(target_year, target_month, 1) > date(2026, 3, 17):
            return date(target_year, target_month, 1)

    return None


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("""
        SELECT id, prediction_text, prediction_date
        FROM mer_predictions
        WHERE is_correct IS NULL AND expected_date IS NULL
    """)

    updates = []
    for r in rows:
        ed = parse_expected_date(r["prediction_text"], r["prediction_date"])
        if ed:
            updates.append((r["id"], ed))

    if updates:
        await conn.executemany(
            "UPDATE mer_predictions SET expected_date = $2 WHERE id = $1",
            updates,
        )

    # 통계
    total_pending = await conn.fetchval(
        "SELECT COUNT(*) FROM mer_predictions WHERE is_correct IS NULL"
    )
    with_date = await conn.fetchval(
        "SELECT COUNT(*) FROM mer_predictions WHERE is_correct IS NULL AND expected_date IS NOT NULL"
    )
    skippable = await conn.fetchval(
        "SELECT COUNT(*) FROM mer_predictions WHERE is_correct IS NULL AND expected_date > CURRENT_DATE"
    )

    print(f"파싱 결과: {len(updates)}건에 expected_date 설정")
    print(f"PENDING 전체: {total_pending}건")
    print(f"  expected_date 있음: {with_date}건")
    print(f"  현재 skip 가능 (미래): {skippable}건")
    print(f"  매일 체크 대상: {total_pending - skippable}건")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
