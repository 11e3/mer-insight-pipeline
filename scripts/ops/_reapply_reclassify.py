"""에러 56건 재파싱 — 코드블록 제거 후 적용."""
import asyncio
import json
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.config.settings import DATABASE_URL

RESULTS = Path("data/reclassify_pending/results.jsonl")


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    correct = incorrect = unverifiable = skipped = errors = 0

    with open(RESULTS, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            pred_id = int(item["custom_id"].replace("rcl-", ""))

            result = item.get("result", {})
            if result.get("type") != "succeeded":
                errors += 1
                continue

            raw = ""
            for block in result.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    raw = block.get("text", "")
                    break

            # 코드블록 제거
            cleaned = re.sub(r"```(?:json)?\s*", "", raw)
            cleaned = re.sub(r"\s*```", "", cleaned).strip()

            m = re.search(r"\{.+\}", cleaned, re.DOTALL)
            if not m:
                errors += 1
                continue

            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                errors += 1
                continue

            # 이미 처리된 건 스킵
            existing = await conn.fetchval(
                "SELECT is_correct FROM mer_predictions WHERE id = $1", pred_id
            )
            if existing is not None:
                skipped += 1
                continue

            verdict = data.get("verdict", "UNVERIFIABLE")
            reason = data.get("reason", "")

            if verdict == "CORRECT":
                await conn.execute(
                    "UPDATE mer_predictions SET is_correct = TRUE, actual_outcome = $1, verification_date = CURRENT_DATE WHERE id = $2",
                    reason, pred_id,
                )
                correct += 1
            elif verdict == "INCORRECT":
                await conn.execute(
                    "UPDATE mer_predictions SET is_correct = FALSE, actual_outcome = $1, verification_date = CURRENT_DATE WHERE id = $2",
                    reason, pred_id,
                )
                incorrect += 1
            else:
                await conn.execute(
                    "UPDATE mer_predictions SET is_verifiable = FALSE WHERE id = $1",
                    pred_id,
                )
                unverifiable += 1

    await conn.close()
    print(f"CORRECT={correct}, INCORRECT={incorrect}, UNVERIFIABLE={unverifiable}, skipped={skipped}, errors={errors}")

    # 에러 건 디버그
    if errors > 0:
        print("\n에러 샘플:")
        with open(RESULTS, encoding="utf-8") as f2:
            shown = 0
            for line2 in f2:
                if shown >= 3:
                    break
                item2 = json.loads(line2)
                result2 = item2.get("result", {})
                if result2.get("type") != "succeeded":
                    print(f"  {item2['custom_id']}: type={result2.get('type')}")
                    shown += 1


if __name__ == "__main__":
    asyncio.run(main())
