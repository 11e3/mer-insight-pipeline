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

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU

log = logging.getLogger(__name__)

BATCH_SIZE = 20  # 한 번에 검증할 예측 수

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

        # 컨텍스트 한 번만 빌드 (가장 오래된 예측일 ~ 오늘)
        oldest = min(p["prediction_date"] for p in preds)
        ctx = await self._build_context(oldest)

        resolved = 0
        for i in range(0, len(preds), BATCH_SIZE):
            batch   = preds[i: i + BATCH_SIZE]
            results = await self._verify_batch(batch, ctx)
            resolved += await self._save_results(results)
            if len(preds) > BATCH_SIZE:
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

    async def _build_context(self, since: date) -> str:
        """since ~ 오늘까지 월별 요약 + auto_analyses."""
        from datetime import date as date_cls
        until = date_cls.today()
        parts: list[str] = []

        # 월별 평균/최고/최저로 압축 (일별로 넣으면 토큰 초과)
        macro_rows = await self.conn.fetch("""
            SELECT
                to_char(date, 'YYYY-MM') AS ym,
                ROUND(AVG(kospi)::numeric, 0)    AS kospi_avg,
                ROUND(MAX(kospi)::numeric, 0)    AS kospi_max,
                ROUND(MIN(kospi)::numeric, 0)    AS kospi_min,
                ROUND(AVG(usd_krw)::numeric, 0)  AS krw_avg,
                ROUND(MAX(usd_krw)::numeric, 0)  AS krw_max,
                ROUND(AVG(wti)::numeric, 1)      AS wti_avg,
                ROUND(MAX(wti)::numeric, 1)      AS wti_max,
                ROUND(AVG(us_10y)::numeric, 2)   AS us10y_avg,
                ROUND(AVG(fed_funds_rate)::numeric, 2) AS fed_avg,
                ROUND(AVG(vix)::numeric, 1)      AS vix_avg,
                ROUND(AVG(btc_usd)::numeric, 0)  AS btc_avg
            FROM macro_daily
            WHERE date BETWEEN $1 AND $2
            GROUP BY ym
            ORDER BY ym
        """, since, until)

        if macro_rows:
            lines = [f"[월별 매크로 요약: {since} ~ {until}]"]
            for r in macro_rows:
                lines.append(
                    f"{r['ym']}: KOSPI={r['kospi_avg']}(고{r['kospi_max']}/저{r['kospi_min']}) "
                    f"USD/KRW={r['krw_avg']}(고{r['krw_max']}) "
                    f"WTI={r['wti_avg']}(고{r['wti_max']}) "
                    f"US10Y={r['us10y_avg']}% Fed={r['fed_avg']}% VIX={r['vix_avg']} BTC={r['btc_avg']}"
                )
            parts.append("\n".join(lines))

        # 해당 기간 메르 분석 (최대 10개)
        analysis_rows = await self.conn.fetch("""
            SELECT e.title, aa.analysis_text, e.event_date
            FROM events e
            JOIN auto_analyses aa ON aa.event_id = e.id
            WHERE e.event_type = 'mer_new_post'
              AND e.event_date::date BETWEEN $1 AND $2
            ORDER BY e.event_date DESC
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

