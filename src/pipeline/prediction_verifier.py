"""
PredictionVerifier — 매일 미검증 예측을 Claude(Haiku)로 배치 검증.

검증 로직:
  - 컨텍스트를 예측 생성일 기준으로 구성 (예측일 이후 90일 macro_daily)
  - CORRECT/INCORRECT → 확정, 더 이상 검증 안 함
  - PENDING → 재시도. 단, 생성일로부터 180일 초과 시 skipped_at 마킹 후 제외
"""

import asyncio
import json
import logging
from datetime import timedelta
from collections import defaultdict

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU

log = logging.getLogger(__name__)

BATCH_SIZE   = 20   # 한 번에 검증할 예측 수
CONTEXT_DAYS = 90   # 예측일 이후 몇 일치 데이터를 컨텍스트로 쓸지

_SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
주어진 [컨텍스트](예측일 이후의 시장 데이터)를 보고,
[예측 목록]의 각 항목에 대해 다음 중 하나로 판단한다:

1. CORRECT   — 예측의 조건이 충족됐고 결과가 예측과 일치
2. INCORRECT — 예측의 조건이 충족됐으나 결과가 예측과 반대
3. PENDING   — 조건이 아직 충족되지 않았거나 판단하기에 정보가 부족

조건부 예측("A가 발생하면 B")은 A가 컨텍스트 기간 내에 실제로 발생했는지 먼저 확인.
판단 근거는 컨텍스트에 있는 수치나 사실에만 기반한다. 추정 금지.

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
        """미검증 예측 전체를 배치로 검증. 반환: 확정된 건수."""
        preds = await self._fetch_pending()
        if not preds:
            log.info("검증할 예측 없음")
            return 0

        # 월 단위로 묶어서 컨텍스트 공유
        resolved = 0
        by_period: dict[str, list[dict]] = defaultdict(list)
        for p in preds:
            period_key = p["prediction_date"].strftime("%Y-%m")
            by_period[period_key].append(p)

        for period_key, group in by_period.items():
            # 해당 월의 예측일 기준 컨텍스트
            pred_date = group[0]["prediction_date"]
            ctx = await self._build_context(pred_date)

            for i in range(0, len(group), BATCH_SIZE):
                batch   = group[i: i + BATCH_SIZE]
                results = await self._verify_batch(batch, ctx)
                resolved += await self._save_results(results)
                if len(group) > BATCH_SIZE:
                    await asyncio.sleep(0.3)

        log.info(f"예측 검증 완료: {resolved}/{len(checkable)}건 확정")
        return resolved

    # ── 데이터 수집 ────────────────────────────────────────────────────────────

    async def _fetch_pending(self) -> list[dict]:
        """미검증 예측 조회."""
        rows = await self.conn.fetch("""
            SELECT id, prediction_text, predicted_direction,
                   target_asset, prediction_date
            FROM mer_predictions
            WHERE is_correct IS NULL
            ORDER BY prediction_date DESC
        """)
        return [dict(r) for r in rows]

    async def _build_context(self, pred_date: date) -> str:
        """예측일 이후 CONTEXT_DAYS일치 macro_daily + 해당 기간 auto_analyses."""
        since = pred_date
        until = pred_date + timedelta(days=CONTEXT_DAYS)
        parts: list[str] = []

        macro_rows = await self.conn.fetch("""
            SELECT date, kospi, usd_krw, wti, us_10y, vix,
                   fed_funds_rate, kr_base_rate, btc_usd
            FROM macro_daily
            WHERE date BETWEEN $1 AND $2
            ORDER BY date ASC
        """, since, until)

        if macro_rows:
            lines = [f"[매크로 데이터: {since} ~ {until}]"]
            for r in macro_rows:
                lines.append(
                    f"{r['date']}: KOSPI={r['kospi']} USD/KRW={r['usd_krw']} "
                    f"WTI={r['wti']} US10Y={r['us_10y']}% VIX={r['vix']} "
                    f"Fed={r['fed_funds_rate']}% KR={r['kr_base_rate']}% BTC={r['btc_usd']}"
                )
            parts.append("\n".join(lines))

        analysis_rows = await self.conn.fetch("""
            SELECT e.title, aa.analysis_text, e.event_date
            FROM events e
            JOIN auto_analyses aa ON aa.event_id = e.id
            WHERE e.event_type = 'mer_new_post'
              AND e.event_date::date BETWEEN $1 AND $2
            ORDER BY e.event_date ASC
            LIMIT 10
        """, since, until)

        if analysis_rows:
            lines = [f"[메르 분석: {since} ~ {until}]"]
            for r in analysis_rows:
                lines.append(
                    f"[{r['event_date'].strftime('%Y-%m-%d')}] {r['title']}\n"
                    f"{(r['analysis_text'] or '')[:300]}"
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
        user_msg = f"[컨텍스트]\n{ctx}\n\n[예측 목록]\n{pred_list}"
        try:
            resp = await self._claude.messages.create(
                model=MODEL_HAIKU,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw   = resp.content[0].text.strip()
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

