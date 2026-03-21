"""
insights (prediction 타입, 최근 1년) → predictions 마이그레이션.

Usage:
    python -m scripts.migrate_predictions
"""

import asyncio
import json
import logging
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("""
            SELECT mi.id, mi.structured_data, mp.date
            FROM insights mi
            JOIN posts mp ON mi.post_id = mp.id
            WHERE mi.insight_type = 'prediction'
              AND NOT EXISTS (
                SELECT 1 FROM predictions mp2
                WHERE mp2.insight_id = mi.id
              )
            ORDER BY mp.date DESC
        """)
        log.info(f"마이그레이션 대상: {len(rows)}건")

        inserted = 0
        skipped  = 0
        for r in rows:
            d = r["structured_data"]
            if isinstance(d, str):
                d = json.loads(d)

            pred_text = d.get("prediction", "")
            direction = d.get("direction", "neutral")
            asset     = d.get("target_asset", "")
            verifiable = d.get("verifiable", True)

            # 검증 불가 or 비경제 예측 스킵
            if not verifiable:
                skipped += 1
                continue
            if not pred_text or not asset:
                skipped += 1
                continue

            await conn.execute("""
                INSERT INTO predictions
                    (insight_id, prediction_text, predicted_direction,
                     target_asset, prediction_date)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING
            """, r["id"], pred_text, direction, asset, r["date"])
            inserted += 1

        log.info(f"완료 — 삽입: {inserted}건, 스킵: {skipped}건")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
