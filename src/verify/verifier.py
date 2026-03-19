"""
PredictionVerifier — expected_date 지난 PENDING 예측을 자동 내보내기 + 텔레그램 알림.

자동 판정은 신뢰도 부족으로 제거됨 (실험 결과 README 참조).
수동 검증은 claude.ai에서 진행하고, import_manual_verdicts.py로 DB 반영.
"""

import logging
import os
from datetime import date
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

EXPORT_DIR = Path("data/manual_verify/pending")
BATCH_SIZE = 100


class PredictionVerifier:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def run(self) -> int:
        """expected_date 지난 미검증 예측 내보내기 + 알림. 반환: 내보낸 건수."""
        preds = await self._fetch_verifiable()
        if not preds:
            log.info("검증 대기 예측 없음")
            return 0

        exported = await self._export(preds)
        await self._notify(len(preds))
        log.info(f"검증 대기 {len(preds)}건 내보내기 완료 → {EXPORT_DIR}")
        return exported

    async def _fetch_verifiable(self) -> list[dict]:
        """expected_date가 지났거나 없는 미검증 예측 조회."""
        rows = await self.conn.fetch("""
            SELECT id, prediction_text, predicted_direction,
                   target_asset, prediction_date, expected_date
            FROM mer_predictions
            WHERE is_correct IS NULL
              AND (expected_date IS NULL OR expected_date <= CURRENT_DATE)
            ORDER BY prediction_date, id
        """)
        return [dict(r) for r in rows]

    async def _export(self, preds: list[dict]) -> int:
        """검증 대기 예측을 텍스트 파일로 내보내기."""
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()

        header = f"""# 수동 검증 대기 — {today} ({len(preds)}건)

아래 예측들을 웹 검색으로 확인 후 CORRECT/INCORRECT/PENDING으로 판정해주세요.
- CORRECT: 예측 조건이 충족됐고 결과가 예측과 일치
- INCORRECT: 예측 조건이 충족됐으나 결과가 예측과 반대
- PENDING: 아직 결과를 알 수 없는 미래 예측 → expected_date 필수

출력 형식 (JSON 배열만, 설명 텍스트 없이):
[{{"id": N, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "한줄 근거", "expected_date": "YYYY-MM-DD (PENDING일 때만)"}}]

---

"""
        total_parts = (len(preds) + BATCH_SIZE - 1) // BATCH_SIZE
        for i in range(0, len(preds), BATCH_SIZE):
            batch = preds[i:i + BATCH_SIZE]
            part = i // BATCH_SIZE + 1
            fname = EXPORT_DIR / f"{today}_part{part:02d}.txt"

            with open(fname, "w", encoding="utf-8") as f:
                f.write(header)
                for p in batch:
                    asset = p["target_asset"] or "기타"
                    d = p["prediction_date"]
                    date_str = d.isoformat() if d else "없음"
                    f.write(
                        f'{p["id"]}. [{asset}] {p["prediction_text"]} '
                        f'(방향: {p["predicted_direction"]}, 예측일: {date_str})\n'
                    )
            log.info(f"  {fname.name}: {len(batch)}건")

        return len(preds)

    async def _notify(self, count: int):
        """텔레그램으로 검증 대기 알림."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_TIER1_CHAT_ID")
        if not token or not chat_id:
            return

        try:
            import asyncio
            import requests
            text = f"📋 예측 검증 대기: {count}건\ndata/manual_verify/pending/ 에서 확인"
            await asyncio.to_thread(
                requests.post,
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            log.warning(f"텔레그램 알림 실패: {e}")
