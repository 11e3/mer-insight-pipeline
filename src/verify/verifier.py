"""
PredictionVerifier — 매일 미검증 예측을 Claude(Haiku)로 배치 검증.
"""

import asyncio
import json
import logging

import anthropic
import asyncpg

from src.config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU
from src.verify.prompt import BATCH_SIZE, SYSTEM

log = logging.getLogger(__name__)


class PredictionVerifier:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
        self._claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def run(self) -> int:
        """미검증 예측 전체를 배치로 검증. 반환: 확정된 건수."""
        preds = await self._fetch_pending()
        if not preds:
            log.info("검증할 예측 없음")
            return 0

        resolved = 0
        for i in range(0, len(preds), BATCH_SIZE):
            batch   = preds[i: i + BATCH_SIZE]
            results = await self._verify_batch(batch)
            resolved += await self._save_results(results)
            if len(preds) > BATCH_SIZE:
                await asyncio.sleep(0.3)

        log.info(f"예측 검증 완료: {resolved}/{len(preds)}건 확정")
        return resolved

    async def _fetch_pending(self) -> list[dict]:
        rows = await self.conn.fetch("""
            SELECT id, prediction_text, predicted_direction,
                   target_asset, prediction_date
            FROM mer_predictions
            WHERE is_correct IS NULL
              AND (expected_date IS NULL OR expected_date <= CURRENT_DATE)
            ORDER BY prediction_date DESC
        """)
        return [dict(r) for r in rows]

    async def _verify_batch(self, batch: list[dict]) -> list[dict]:
        pred_list = "\n".join(
            f'{p["id"]}. [{p["target_asset"]}] {p["prediction_text"]} '
            f'(방향: {p["predicted_direction"]}, 예측일: {p["prediction_date"]})'
            for p in batch
        )
        try:
            resp = await self._claude.messages.create(
                model=MODEL_HAIKU,
                max_tokens=8192,
                system=[{
                    "type": "text",
                    "text": SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": f"[예측 목록]\n{pred_list}"}],
            )
            raw = next((b.text for b in resp.content if hasattr(b, "text")), "").strip()

            cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
            if cache_read or cache_create:
                log.debug(
                    f"토큰: input={resp.usage.input_tokens} "
                    f"cache_read={cache_read} cache_create={cache_create} "
                    f"output={resp.usage.output_tokens}"
                )

            if resp.stop_reason == "max_tokens":
                log.warning(
                    f"응답 truncated (max_tokens). "
                    f"input={resp.usage.input_tokens} output={resp.usage.output_tokens}"
                )

            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])

            log.warning(f"JSON 파싱 불가. stop={resp.stop_reason} raw[:200]={raw[:200]!r}")
            return []
        except json.JSONDecodeError as e:
            log.error(f"JSON 디코딩 오류: {e}. raw[:200]={raw[:200]!r}")
            return []
        except Exception as e:
            log.error(f"배치 검증 오류: {e}")
            return []

    async def _save_results(self, results: list[dict]) -> int:
        resolved = 0
        for r in results:
            verdict = r.get("verdict", "PENDING")
            if verdict == "PENDING":
                continue
            pred_id = r.get("id")
            if not pred_id:
                continue
            await self.conn.execute("""
                UPDATE mer_predictions
                SET is_correct        = $1,
                    actual_outcome    = $2,
                    verification_date = CURRENT_DATE
                WHERE id = $3 AND is_correct IS NULL
            """, verdict == "CORRECT", r.get("reason", ""), pred_id)
            resolved += 1
        return resolved
