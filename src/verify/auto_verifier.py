"""헤드라인 매칭 기반 자동 검증 — Haiku 1건씩 호출."""

import asyncio
import json
import logging
import re

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
                verdict_data, valid_urls = await self._verify_single(match)

                input_t = verdict_data.pop("_input_tokens", 0)
                output_t = verdict_data.pop("_output_tokens", 0)
                total_input += input_t
                total_output += output_t

                verdict = verdict_data.get("verdict", "PENDING")
                source_url = verdict_data.get("source_url", "")
                reason = verdict_data.get("reason", "")

                # source_url 검증: 헤드라인 URL 목록에 있는지 확인
                if source_url and valid_urls and source_url not in valid_urls:
                    log.warning(
                        f"  #{match['prediction_id']}: source_url 환각 감지 — {source_url[:60]}"
                    )
                    source_url = ""  # 환각 URL은 비움 → PENDING 처리됨

                if verdict in ("CORRECT", "INCORRECT") and source_url:
                    is_correct = verdict == "CORRECT"
                    await self.conn.execute("""
                        UPDATE mer_predictions
                        SET is_correct = $1,
                            actual_outcome = $2,
                            source_url = $3,
                            verification_date = CURRENT_DATE
                        WHERE id = $4 AND is_correct IS NULL
                    """, is_correct, reason, source_url, match["prediction_id"])
                    result["auto_resolved"] += 1
                elif verdict in ("CORRECT", "INCORRECT") and not source_url:
                    log.warning(
                        f"  #{match['prediction_id']}: {verdict} but no source_url — skipped"
                    )
                    result["pending"] += 1
                else:
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

    async def _verify_single(self, match: dict) -> tuple[dict, set]:
        """단일 예측에 대해 Haiku 호출 → (verdict_data, valid_urls) 반환."""
        from datetime import date as _date

        headlines_text = "\n".join(
            f"{i+1}. {h['headline']} ({h['published_at']}, {h['source_url']})"
            for i, h in enumerate(match["headlines"])
        )

        # 유효한 source_url 목록 (검증용, 로컬 변수)
        valid_urls = {h["source_url"] for h in match["headlines"] if h.get("source_url")}

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
        data = self._parse_json(raw)
        data["_input_tokens"] = resp.usage.input_tokens
        data["_output_tokens"] = resp.usage.output_tokens
        return data, valid_urls

    @staticmethod
    def _parse_json(text: str) -> dict:
        """응답에서 JSON 추출."""
        # 코드블록 제거
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # fallback: greedy JSON 객체 찾기 (reason에 {}가 포함될 수 있음)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group())
                if "verdict" in obj:
                    return obj
            except json.JSONDecodeError:
                pass

        return {"verdict": "PENDING", "reason": "JSON 파싱 실패", "source_url": ""}
