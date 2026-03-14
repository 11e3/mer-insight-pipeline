"""
PredictionVerifier — 매일 미검증 예측을 Claude(Haiku)로 배치 검증.

조건부 예측 처리:
  - 조건 아직 미충족 → 패스 (내일 재확인)
  - 조건 충족 + 결과 명확 → is_correct / actual_outcome 업데이트
"""

import asyncio
import json
import logging
from datetime import date, timedelta

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU

log = logging.getLogger(__name__)

BATCH_SIZE = 20  # 한 번에 검증할 예측 수

_SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
주어진 [컨텍스트](최근 시장 데이터 + 분석 텍스트)를 보고,
[예측 목록]의 각 항목에 대해 다음 중 하나로 판단한다:

1. CORRECT   — 예측의 조건이 충족됐고 결과가 예측과 일치
2. INCORRECT — 예측의 조건이 충족됐으나 결과가 예측과 반대
3. PENDING   — 조건이 아직 충족되지 않았거나 판단하기에 정보가 부족

조건부 예측("A가 발생하면 B")은 반드시 A가 실제로 발생했는지 먼저 확인.
판단 근거는 컨텍스트에 있는 수치나 사실에만 기반한다.

출력: 반드시 JSON 배열만. 설명 텍스트 없이.
[
  {"id": 예측ID, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거 한 줄 (한국어)"},
  ...
]
"""


class PredictionVerifier:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
        self._claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def run(self) -> int:
        """미검증 예측 전체를 배치로 검증. 반환: 처리된 건수."""
        preds = await self._fetch_pending()
        if not preds:
            log.info("검증할 예측 없음")
            return 0

        ctx = await self._build_context()
        resolved = 0

        for i in range(0, len(preds), BATCH_SIZE):
            batch = preds[i: i + BATCH_SIZE]
            results = await self._verify_batch(batch, ctx)
            resolved += await self._save_results(results)
            if len(preds) > BATCH_SIZE:
                await asyncio.sleep(0.5)

        log.info(f"예측 검증 완료: {resolved}/{len(preds)}건 확정")
        return resolved

    # ── 데이터 수집 ────────────────────────────────────────────────────────────

    async def _fetch_pending(self) -> list[dict]:
        rows = await self.conn.fetch("""
            SELECT id, prediction_text, predicted_direction,
                   target_asset, prediction_date
            FROM mer_predictions
            WHERE is_correct IS NULL
            ORDER BY prediction_date DESC
        """)
        return [dict(r) for r in rows]

    async def _build_context(self) -> str:
        """최근 14일 macro_daily + 최근 auto_analyses 텍스트."""
        parts: list[str] = []

        # 매크로 수치
        macro_rows = await self.conn.fetch("""
            SELECT date, kospi, usd_krw, wti, us_10y, vix,
                   fed_funds_rate, kr_base_rate, btc_usd
            FROM macro_daily
            WHERE date >= CURRENT_DATE - INTERVAL '14 days'
            ORDER BY date DESC
        """)
        if macro_rows:
            lines = ["[매크로 데이터 최근 14일]"]
            for r in macro_rows:
                lines.append(
                    f"{r['date']}: KOSPI={r['kospi']} USD/KRW={r['usd_krw']} "
                    f"WTI={r['wti']} US10Y={r['us_10y']}% VIX={r['vix']} "
                    f"Fed={r['fed_funds_rate']}% KR={r['kr_base_rate']}% BTC={r['btc_usd']}"
                )
            parts.append("\n".join(lines))

        # 메르 최근 분석
        analysis_rows = await self.conn.fetch("""
            SELECT e.title, aa.analysis_text, e.event_date
            FROM events e
            JOIN auto_analyses aa ON aa.event_id = e.id
            WHERE e.event_type = 'mer_new_post'
              AND e.event_date >= NOW() - INTERVAL '14 days'
            ORDER BY e.event_date DESC
            LIMIT 10
        """)
        if analysis_rows:
            lines = ["[최근 14일 메르 분석]"]
            for r in analysis_rows:
                lines.append(
                    f"[{r['event_date'].strftime('%Y-%m-%d')}] {r['title']}\n"
                    f"{(r['analysis_text'] or '')[:400]}"
                )
            parts.append("\n\n".join(lines))

        return "\n\n".join(parts) if parts else "(컨텍스트 없음)"

    # ── Claude 검증 ────────────────────────────────────────────────────────────

    async def _verify_batch(self, batch: list[dict], ctx: str) -> list[dict]:
        pred_list = "\n".join(
            f'{p["id"]}. [{p["target_asset"]}] {p["prediction_text"]} '
            f'(방향: {p["predicted_direction"]}, 예측일: {p["prediction_date"]})'
            for p in batch
        )
        user_msg = (
            f"[컨텍스트]\n{ctx}\n\n"
            f"[예측 목록]\n{pred_list}"
        )
        try:
            resp = await self._claude.messages.create(
                model=MODEL_HAIKU,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            # JSON 배열 추출
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            return json.loads(raw[start:end]) if start >= 0 else []
        except Exception as e:
            log.error(f"배치 검증 오류: {e}")
            return []

    # ── 결과 저장 ──────────────────────────────────────────────────────────────

    async def _save_results(self, results: list[dict]) -> int:
        resolved = 0
        for r in results:
            verdict = r.get("verdict", "PENDING")
            if verdict == "PENDING":
                continue
            is_correct = verdict == "CORRECT"
            reason     = r.get("reason", "")
            pred_id    = r.get("id")
            if not pred_id:
                continue
            await self.conn.execute("""
                UPDATE mer_predictions
                SET is_correct     = $1,
                    actual_outcome = $2,
                    verification_date = CURRENT_DATE
                WHERE id = $3 AND is_correct IS NULL
            """, is_correct, reason, pred_id)
            resolved += 1
        return resolved
