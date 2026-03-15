"""
PredictionVerifier — 매일 미검증 예측을 Claude(Haiku)로 배치 검증.

컨텍스트:
  - 가장 오래된 예측일 ~ 오늘까지 월별 macro 요약
  - 주요 한국 주식 월별 종가 (네이버 금융)
  - 해당 기간 auto_analyses

판단:
  - CORRECT/INCORRECT → 확정
  - PENDING → 결론날 때까지 매일 재시도
"""

import asyncio
import json
import logging
from datetime import date
from xml.etree import ElementTree as ET

import anthropic
import asyncpg
import requests

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU

log = logging.getLogger(__name__)

BATCH_SIZE = 40

# 예측에 자주 등장하는 한국 주식 종목코드
KR_STOCKS = {
    "삼성전자":   "005930",
    "SK하이닉스": "000660",
    "현대차":     "005380",
    "기아":       "000270",
    "LG에너지솔루션": "373220",
    "삼성SDI":    "006400",
    "POSCO홀딩스": "005490",
    "카카오":     "035720",
    "네이버":     "035420",
    "셀트리온":   "068270",
}

_HEADERS = {"User-Agent": "Mozilla/5.0"}

_SYSTEM = """\
너는 경제·금융 예측 검증 전문가다.
[컨텍스트]와 네 자신의 지식을 모두 활용해 [예측 목록]의 각 항목을 판단한다.

판단 기준:
1. CORRECT   — 예측 조건이 충족됐고 결과가 예측과 일치
2. INCORRECT — 예측 조건이 충족됐으나 결과가 예측과 반대
3. PENDING   — 조건이 아직 충족되지 않았거나 충분한 정보가 없어 판단 불가

규칙:
- 조건부 예측("A가 발생하면 B")은 A가 실제로 발생했는지 먼저 확인
- 컨텍스트 데이터 우선. 없으면 네 지식으로 보완 가능 (단, 확신이 없으면 PENDING)
- 예측일 이후 기간만 기준으로 판단
- reason은 근거를 구체적으로 한 줄로 (수치 포함 권장)

출력: JSON 배열만. 설명 텍스트 없이.
[
  {"id": 예측ID, "verdict": "CORRECT|INCORRECT|PENDING", "reason": "판단 근거 (한국어)"},
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

        oldest = min(p["prediction_date"] for p in preds)
        ctx    = await self._build_context(oldest)

        resolved = 0
        for i in range(0, len(preds), BATCH_SIZE):
            batch   = preds[i: i + BATCH_SIZE]
            results = await self._verify_batch(batch, ctx)
            resolved += await self._save_results(results)
            if len(preds) > BATCH_SIZE:
                await asyncio.sleep(0.3)

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

    async def _build_context(self, since: date) -> str:
        until = date.today()
        parts: list[str] = []

        # 1. 월별 macro 요약
        macro_rows = await self.conn.fetch("""
            SELECT
                to_char(date, 'YYYY-MM') AS ym,
                ROUND(AVG(kospi)::numeric, 0)          AS kospi_avg,
                ROUND(MAX(kospi)::numeric, 0)          AS kospi_max,
                ROUND(MIN(kospi)::numeric, 0)          AS kospi_min,
                ROUND(AVG(usd_krw)::numeric, 0)        AS krw_avg,
                ROUND(MAX(usd_krw)::numeric, 0)        AS krw_max,
                ROUND(AVG(wti)::numeric, 1)            AS wti_avg,
                ROUND(MAX(wti)::numeric, 1)            AS wti_max,
                ROUND(AVG(us_10y)::numeric, 2)         AS us10y_avg,
                ROUND(AVG(fed_funds_rate)::numeric, 2) AS fed_avg,
                ROUND(AVG(vix)::numeric, 1)            AS vix_avg,
                ROUND(AVG(btc_usd)::numeric, 0)        AS btc_avg
            FROM macro_daily
            WHERE date BETWEEN $1 AND $2
            GROUP BY ym ORDER BY ym
        """, since, until)

        if macro_rows:
            lines = [f"[월별 매크로 (고/저/평균): {since} ~ {until}]"]
            for r in macro_rows:
                lines.append(
                    f"{r['ym']}: KOSPI={r['kospi_avg']}(고{r['kospi_max']}/저{r['kospi_min']}) "
                    f"USD/KRW={r['krw_avg']}(고{r['krw_max']}) "
                    f"WTI={r['wti_avg']}(고{r['wti_max']}) "
                    f"US10Y={r['us10y_avg']}% Fed={r['fed_avg']}% VIX={r['vix_avg']} BTC={r['btc_avg']}"
                )
            parts.append("\n".join(lines))

        # 1-1. 최근 30일 일간 매크로
        recent_rows = await self.conn.fetch("""
            SELECT date, kospi, usd_krw, wti, us_10y, vix, btc_usd
            FROM macro_daily
            WHERE date BETWEEN CURRENT_DATE - INTERVAL '30 days' AND $1
            ORDER BY date
        """, until)
        if recent_rows:
            lines = ["[최근 30일 일간 매크로]"]
            for r in recent_rows:
                lines.append(
                    f"{r['date']}: KOSPI={r['kospi'] and int(r['kospi'])} "
                    f"KRW={r['usd_krw'] and int(r['usd_krw'])} "
                    f"WTI={r['wti']} US10Y={r['us_10y']} VIX={r['vix']} BTC={r['btc_usd'] and int(r['btc_usd'])}"
                )
            parts.append("\n".join(lines))

        # 2. 주요 한국 주식 월별 종가
        stock_ctx = await asyncio.get_event_loop().run_in_executor(
            None, self._fetch_stock_context, since, until
        )
        if stock_ctx:
            parts.append(stock_ctx)

        # 3. 해당 기간 메르 분석
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

        # 4. DART 공시 + 뉴스 (기업 이벤트 검증용)
        event_rows = await self.conn.fetch("""
            SELECT event_type, title, content, event_date
            FROM events
            WHERE event_type IN ('dart', 'news')
              AND event_date::date BETWEEN $1 AND $2
            ORDER BY event_date DESC
            LIMIT 20
        """, since, until)

        if event_rows:
            lines = [f"[DART 공시 · 뉴스: {since} ~ {until}]"]
            for r in event_rows:
                tag = "DART" if r["event_type"] == "dart" else "뉴스"
                lines.append(
                    f"[{r['event_date'].strftime('%Y-%m-%d')} {tag}] {r['title']}\n"
                    f"{(r['content'] or '')[:150]}"
                )
            parts.append("\n\n".join(lines))

        return "\n\n".join(parts) if parts else "(컨텍스트 없음)"

    def _fetch_stock_context(self, since: date, until: date) -> str:
        """네이버 금융에서 주요 종목 주가 수집.

        - 최근 30일: 일간 종가
        - 이전: 월별 고/저/말일 종가 (H/L/C)
        """
        days = (until - since).days
        count = min(days + 30, 1800)  # 최대 약 5년치
        cutoff = date(until.year, until.month, until.day)
        # 최근 90일 기준일
        from datetime import timedelta
        recent_since = until - timedelta(days=30)

        lines = ["[주요 한국 주식 주가]"]
        fetched = 0

        for name, code in KR_STOCKS.items():
            try:
                url = (
                    f"https://fchart.stock.naver.com/sise.nhn"
                    f"?symbol={code}&timeframe=day&count={count}&requestType=0"
                )
                resp = requests.get(url, headers=_HEADERS, timeout=10)
                root = ET.fromstring(resp.content.decode("euc-kr", errors="replace"))

                by_month: dict[str, list[int]] = {}  # ym -> [closes]
                daily_recent: list[str] = []          # 최근 30일 일간

                for item in root.findall(".//item"):
                    raw = item.get("data", "")
                    parts = raw.split("|")
                    if len(parts) < 5:
                        continue
                    dt, close = parts[0], parts[4]
                    if not dt or not close:
                        continue
                    try:
                        d = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
                        c = int(close)
                    except ValueError:
                        continue
                    if d < since or d > until:
                        continue

                    if d >= recent_since:
                        daily_recent.append(f"{d}={c:,}")
                    else:
                        ym = dt[:6]
                        by_month.setdefault(ym, []).append(c)

                parts_line: list[str] = []

                # 월별 H/L/C (90일 이전)
                if by_month:
                    monthly = " ".join(
                        f"{ym[:4]}-{ym[4:]}(H={max(v):,}/L={min(v):,}/C={v[-1]:,})"
                        for ym, v in sorted(by_month.items())
                    )
                    parts_line.append(monthly)

                # 최근 30일 일간 종가
                if daily_recent:
                    parts_line.append("최근30일:" + " ".join(daily_recent))

                if parts_line:
                    lines.append(f"{name}: " + " | ".join(parts_line))
                    fetched += 1

            except Exception as e:
                log.debug(f"주가 수집 실패 ({name}): {e}")

        return "\n".join(lines) if fetched else ""

    # ── Claude 검증 ────────────────────────────────────────────────────────────

    async def _verify_batch(self, batch: list[dict], ctx: str) -> list[dict]:
        pred_list = "\n".join(
            f'{p["id"]}. [{p["target_asset"]}] {p["prediction_text"]} '
            f'(방향: {p["predicted_direction"]}, 예측일: {p["prediction_date"]})'
            for p in batch
        )
        try:
            resp = await self._claude.messages.create(
                model=MODEL_HAIKU,
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": _SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": [
                    {
                        "type": "text",
                        "text": f"[컨텍스트]\n{ctx}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": f"[예측 목록]\n{pred_list}",
                    },
                ]}],
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
