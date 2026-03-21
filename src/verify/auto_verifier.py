"""헤드라인 매칭 기반 자동 검증 — Haiku 1건씩 호출."""

import asyncio
import logging

import anthropic
import asyncpg

from src.config.settings import ANTHROPIC_API_KEY
from src.verify.headline_matcher import batch_match
from src.verify.prompt import (
    AUTO_VERIFY_MODEL,
    AUTO_VERIFY_SYSTEM_PROMPT,
    AUTO_VERIFY_USER_TEMPLATE,
    CALL_DELAY,
    DAILY_LIMIT,
    HAIKU_INPUT_COST_PER_M,
    HAIKU_OUTPUT_COST_PER_M,
    parse_verdict_json,
)

log = logging.getLogger(__name__)


class AutoVerifier:
    """헤드라인 매칭 기반 자동 검증."""

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
        self.client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def run(self, daily_limit: int = DAILY_LIMIT) -> dict:
        """자동 검증 실행.

        Returns: {"auto_resolved": int, "pending": int, "errors": int, "cost_usd": float}
        """
        result = {"auto_resolved": 0, "pending": 0, "errors": 0, "cost_usd": 0.0}

        # 1. 매칭 가능한 예측 조회
        matches = await batch_match(self.conn, limit=daily_limit)
        if not matches:
            log.info("자동 검증: 매칭된 예측 0건")
            return result

        log.info(f"자동 검증: {len(matches)}건 매칭, Haiku 판정 시작")
        total_input = 0
        total_output = 0

        for i, match in enumerate(matches[:daily_limit]):
            try:
                verdict_data = await self._verify_single(match)

                input_t = verdict_data.pop("_input_tokens", 0)
                output_t = verdict_data.pop("_output_tokens", 0)
                total_input += input_t
                total_output += output_t

                verdict = verdict_data.get("verdict", "PENDING")
                reason = verdict_data.get("reason", "")

                # source_url: overlap 최고인 첫 번째 헤드라인 URL (이미 정렬됨)
                source_url = match["headlines"][0]["source_url"] if match.get("headlines") else ""

                if verdict in ("CORRECT", "INCORRECT") and source_url:
                    is_correct = verdict == "CORRECT"
                    status = await self.conn.execute("""
                        UPDATE mer_predictions
                        SET is_correct = $1,
                            actual_outcome = $2,
                            source_url = $3,
                            verification_date = CURRENT_DATE
                        WHERE id = $4 AND is_correct IS NULL
                    """, is_correct, reason, source_url, match["prediction_id"])
                    if status == "UPDATE 1":
                        result["auto_resolved"] += 1
                    else:
                        log.warning(f"  #{match['prediction_id']}: 이미 검증됨 — 스킵")
                        result["pending"] += 1
                else:
                    # PENDING → skipped_at 마킹 (7일간 재시도 방지)
                    await self.conn.execute(
                        "UPDATE mer_predictions SET skipped_at = CURRENT_DATE WHERE id = $1",
                        match["prediction_id"],
                    )
                    result["pending"] += 1

            except Exception as e:
                log.error(f"  #{match['prediction_id']}: API 오류 — {e}")
                result["errors"] += 1

            if i < len(matches) - 1:
                await asyncio.sleep(CALL_DELAY)

        result["cost_usd"] = (
            total_input * HAIKU_INPUT_COST_PER_M
            + total_output * HAIKU_OUTPUT_COST_PER_M
        ) / 1_000_000

        log.info(
            f"자동 검증 완료: {result['auto_resolved']}건 확정, "
            f"{result['pending']}건 보류, {result['errors']}건 오류, "
            f"비용 ${result['cost_usd']:.4f}"
        )
        return result

    async def _verify_single(self, match: dict) -> dict:
        """단일 예측에 대해 Haiku 호출 → verdict_data 반환."""
        from datetime import date as _date

        headlines_text = "\n".join(
            f"{i+1}. {h['headline']} ({h['published_at']})"
            for i, h in enumerate(match["headlines"])
        )

        user_msg = AUTO_VERIFY_USER_TEMPLATE.format(
            today=_date.today().isoformat(),
            prediction_text=match["prediction_text"],
            target_asset=match.get("target_asset", "") or "",
            predicted_direction=match.get("predicted_direction", "neutral"),
            prediction_date=match.get("prediction_date", ""),
            expected_date=match.get("expected_date", "미지정"),
            headlines_text=headlines_text,
        )

        resp = await self.client.messages.create(
            model=AUTO_VERIFY_MODEL,
            max_tokens=256,
            system=AUTO_VERIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = resp.content[0].text.strip()
        # JSON 파싱
        data = parse_verdict_json(raw)
        data["_input_tokens"] = resp.usage.input_tokens
        data["_output_tokens"] = resp.usage.output_tokens
        return data

