"""
주기적 시장 리포트 생성기 — 계층형 추상화 구조.

각 리포트는 하위 리포트를 원재료로 받아 상위 관점 해석을 새로 생성.

  - 일간  (21:00)            — 그날 메르 글 팩트 요약          (Claude 1회)
  - 주간  (월요일 08:00)     — 일간 7개 → 주간 흐름 해석       (Claude 1회)
  - 월간  (매월 1일 08:30)   — 주간 4개 → 월간 패턴·연결고리   (Claude 1회)
  - 분기  (분기 첫날 09:00)  — 월간 3개 → 분기 관심사 이동     (Claude 1회)
  - 연간  (1월 1일 10:00)    — 분기 4개 → 연간 총평            (Claude 2회)
"""

import logging
from datetime import date, timedelta

import asyncpg
import anthropic

from config.settings import ANTHROPIC_API_KEY, MODEL_SONNET
from src.delivery.telegram_bot import TelegramBot

log    = logging.getLogger(__name__)
client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# ─── 시스템 프롬프트 ──────────────────────────────────────────────────────────

_COMMENTARY_SYSTEM = """\
당신은 시장 분석가예요.
아래 [DATA] 블록의 수치를 바탕으로 [COMMENTARY] 슬롯만 채워요.

규칙:
- [DATA]에 없는 수치를 새로 만들거나 추정하지 않아요
- 해요체("~해요 / ~예요") 사용. "~입니다 / ~합니다" 금지
- 한자(漢字) 금지 — 한글로만
- ⚠️ 구체적 수치를 반드시 해석 문장 안에 포함해요 (예: "KOSPI가 -10.6% 급락하면서 VIX가 29.5까지 치솟았어요")
- "큰 폭으로", "상당히" 같은 모호한 표현 대신 반드시 수치를 써요
- 단순 지표 나열 금지 — 지표 간 인과관계·상충 신호를 해석해요
- 요청한 글자 수를 넘지 않아요
- *굵게* 는 *별표 하나*, 이모지는 문단 첫머리에만
- 마크다운 표·헤더(##)·수평선(---) 금지
"""

_SYNTHESIS_SYSTEM = """\
당신은 시장 분석가예요.
[하위 리포트]들을 원재료(input)로 받아, 상위 관점에서 새로운 해석을 생성해요.

절대 금지:
- 하위 리포트를 그대로 이어붙이거나 단순 요약하는 것
- 팩트·수치를 단순 반복하는 것
- 개별 지표를 순서대로 나열하는 것

해야 할 것:
- 하위 리포트에서 개별로는 안 보이던 연결고리와 패턴 발견
- 시간 흐름에 따른 관심사 변화와 구조적 함의 해석
- 상위 레벨에서만 보이는 흐름 (예: "1주차 부동산 대출 → 3주차 사모펀드 → 4주차 리스크 헤징 = 비은행 신용 리스크 추적 중")
- ⚠️ 지표 간 엇갈림·모순을 2~3개 찾아 해석 (예: "수출 호조인데 환율이 오히려 오른 건 금리차에 의한 자본 유출 압력", "VIX가 내려갔는데 BTC가 빠진 건 유동성이 전통자산으로만 선별 유입")
- ⚠️ 구체적 수치를 반드시 해석 문장 안에 포함 (예: "KOSPI -3.2% 급락하며", "VIX 29까지 치솟아")

글쓰기 규칙:
- 해요체("~해요 / ~예요"). "~입니다 / ~합니다" 금지
- 한자(漢字) 금지
- *굵게* = 별표 하나, 이모지는 문단 첫머리에만
- 마크다운 표·헤더(##)·수평선(---) 금지
- 요청한 글자 수를 넘지 않아요
"""


# ─── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _fl(v) -> float:
    if v is None:
        return 0.0
    return float(v)


def _delta_str(v1, v2, unit: str = "", fmt: str = ",.1f") -> str:
    """v1 → v2 변화 문자열. 예: '2,543 → 2,521 ▼ -0.9%'"""
    f1, f2 = _fl(v1), _fl(v2)
    if f1 == 0:
        return f"{f2:{fmt}}{unit}"
    chg = f2 - f1
    pct = chg / abs(f1) * 100
    arrow = "▲" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    return f"{f1:{fmt}}{unit} → {f2:{fmt}}{unit}  {arrow} {sign}{pct:.1f}%"


# ─── 토픽 필터 (블랙리스트) ───────────────────────────────────────────────────
# 경제·금융과 무관한 도메인 레이블을 걸러낸다.
# "조선"(조선업)은 통과, "조선시대"는 차단.

_NON_ECON_KEYWORDS = {
    "조선시대", "고려시대", "조선왕조", "역사",
    "법제", "형벌", "처벌", "사형", "옥사", "형사",
    "의학", "건강", "질병", "치료", "의약", "의료",
    "음식", "식품", "요리", "맛집", "레시피",
    "여행", "관광", "육아", "보육",
    "교육", "학교", "입시", "수능",
    "종교", "불교", "기독교",
    "스포츠", "날씨", "기후",
    "문학", "예술", "영화", "드라마", "연예",
}


def _is_econ_topic(topic: str) -> bool:
    t = topic.lower()
    return not any(kw in t for kw in _NON_ECON_KEYWORDS)


def _topic_line(topics: list[dict], n: int = 5) -> str:
    """경제 토픽만 n개 — '거시경제(18) · 부동산(12) · 조선(9)'"""
    econ = [t for t in topics if t.get("topic") and _is_econ_topic(t["topic"])]
    result = " · ".join(f"{t['topic']}({t['cnt']})" for t in econ[:n])
    return result or "(데이터 없음)"


# ─── 렌더러 (Claude [DATA] 블록 생성용) ───────────────────────────────────────

def _render_macro_block(ms: dict, me: dict) -> str:
    FIELDS = [
        ("KOSPI",        "kospi",          "",   ",.0f"),
        ("KOSDAQ",       "kosdaq",         "",   ",.0f"),
        ("USD/KRW",      "usd_krw",        "원", ",.0f"),
        ("미 10년물",    "us_10y",         "%",  ".2f"),
        ("한국 기준금리","kr_base_rate",   "%",  ".2f"),
        ("Fed 금리",     "fed_funds_rate", "%",  ".2f"),
        ("WTI",          "wti",            "$",  ".1f"),
        ("BTC",          "btc_usd",        "$",  ",.0f"),
        ("VIX",          "vix",            "",   ".1f"),
        ("미국 CPI",     "us_cpi_yoy",     "%",  ".1f"),
        ("미국 실업률",  "us_unemployment","%",  ".1f"),
    ]
    lines = []
    for label, key, unit, fmt in FIELDS:
        v2 = me.get(key)
        if v2 is None:
            continue
        lines.append(f"- {label}: {_delta_str(ms.get(key), v2, unit, fmt)}")
    return "\n".join(lines)


def _render_trade_block(trade: list[dict]) -> str:
    if not trade:
        return "- (데이터 없음)"
    lines = []
    for t in trade:
        exp = _fl(t.get("export_total"))
        imp = _fl(t.get("import_total"))
        bal = _fl(t.get("trade_balance"))
        sign = "+" if bal >= 0 else ""
        lines.append(
            f"- {t['ym']}: 수출 {exp:,.0f} / 수입 {imp:,.0f} / 수지 {sign}{bal:,.0f} (백만달러)"
        )
    return "\n".join(lines)


def _render_topics_block(topics: list[dict]) -> str:
    if not topics:
        return "- (데이터 없음)"
    econ = [t for t in topics if t.get("topic") and _is_econ_topic(t["topic"])]
    if not econ:
        return "- (경제 관련 토픽 없음)"
    total = sum(t["cnt"] for t in topics) or 1
    lines = []
    for t in econ[:8]:
        pct = t["cnt"] / total * 100
        lines.append(f"- {t['topic']}: {t['cnt']}회 ({pct:.0f}%)")
    return "\n".join(lines)


def _render_entities_block(entities: list[dict], limit: int = 15) -> str:
    if not entities:
        return "- (데이터 없음)"
    return "\n".join(
        f"{i}. {e['entity']} ({e['cnt']}회)"
        for i, e in enumerate(entities[:limit], 1)
    )


def _render_predictions_block(preds: list[dict]) -> str:
    if not preds:
        return "- (검증된 예측 없음)"
    correct = sum(1 for p in preds if p.get("is_correct"))
    total   = len(preds)
    lines   = [f"적중률: {correct}/{total} ({correct/total*100:.0f}%)"]
    for p in preds[:10]:
        mark  = "✅" if p.get("is_correct") else "❌"
        asset = p.get("target_asset", "")
        text  = (p.get("prediction_text") or "")[:60]
        lines.append(f"{mark} [{asset}] {text}")
    return "\n".join(lines)


# ─── ReportGenerator ──────────────────────────────────────────────────────────

class ReportGenerator:

    def __init__(self, conn: asyncpg.Connection, telegram: TelegramBot):
        self.conn     = conn
        self.telegram = telegram

    # ─── Public ──────────────────────────────────────────────────────────────

    async def generate_daily(self):
        """일간 리포트 — 그날 메르 글 팩트 요약. 글 없으면 미발송."""
        today = date.today()
        log.info(f"일간 리포트: {today}")

        posts = await self.conn.fetch("""
            SELECT e.title, aa.analysis_text
            FROM events e
            JOIN auto_analyses aa ON aa.event_id = e.id
            WHERE e.event_date::date = $1
              AND e.event_type = 'mer_new_post'
            ORDER BY e.event_date
        """, today)

        if not posts:
            log.info("오늘 메르 글 없음 — 일간 리포트 미발송")
            return

        posts_ctx = "\n\n".join(
            f"[{r['title']}]\n{(r['analysis_text'] or '')[:300]}"
            for r in posts
        )
        summary = await self._commentary(
            posts_ctx,
            "오늘 메르가 쓴 글들을 글별로 정리해요.\n"
            "각 글마다 📋 *제목* — 핵심 팩트 + 시장에 왜 중요한지 한 줄 해석.\n"
            "글과 글 사이는 반드시 줄바꿈(\\n)으로 구분해요.\n"
            "단순 팩트 나열 금지 — '그래서 어떤 의미인지'를 짧게 붙여요.\n"
            "전체 400자 이내.",
            400,
        )
        title = "*📝 일간 메르 인사이트*"
        text  = f"{title}\n_{today.strftime('%Y.%m.%d')}_\n\n{summary}"
        await self.telegram.send_raw("tier2", text)
        await self._save("report_daily", title, text)

    async def generate_weekly(self):
        today = date.today()
        since = today - timedelta(days=7)
        log.info(f"주간 리포트: {since} ~ {today}")

        sub_reports = await self._fetch_sub_reports("report_daily", since, today)
        data        = await self._collect_data(since, today, "weekly")

        if sub_reports:
            narrative = await self._synthesis(
                sub_reports,
                "7개 일간 리포트에서 이번 주 흐름을 3~4문장으로 해석해요.\n"
                "개별 일간 요약의 단순 나열 금지.\n"
                "한 주에 걸쳐 발전한 흐름, 주제 간 연결, 눈에 띄는 패턴을 중심으로.\n"
                "수치를 해석 문장 안에 자연스럽게 써요 (예: 'KOSPI -3.2% 급락하며', 'VIX 29까지 치솟아').\n"
                "수치만 나열하는 건 금지, 해석 없이 수치만 쓰는 것도 금지.\n"
                "200자 이내.",
                200,
            )
        else:
            ctx = (
                f"=== 주간 매크로 변화 ===\n"
                f"{_render_macro_block(data.get('macro_start') or {}, data.get('macro_end') or {})}\n\n"
                f"=== 메르 관심 토픽 ===\n"
                f"{_render_topics_block(data.get('topics') or [])}"
            )
            narrative = await self._commentary(
                ctx,
                "이번 주 시장 흐름을 3~4문장으로 해석해요.\n"
                "수치를 해석 문장 안에 자연스럽게 써요 (예: 'KOSPI -3.2% 급락하며', 'VIX 29까지 치솟아').\n"
                "지표 간 인과관계·상충 신호 중심. 수치만 나열하는 건 금지. 200자 이내.",
                200,
            )

        title = "*📊 주간 시장 리포트*"
        text  = (
            f"{title}\n"
            f"_{since.strftime('%Y.%m.%d')} ~ {today.strftime('%Y.%m.%d')}_\n\n"
            f"{narrative}\n\n"
            f"*이번 주 메르 관심 토픽*\n"
            f"{_topic_line(data.get('topics') or [])}"
        )
        await self.telegram.send_raw("tier2", text)
        await self._save("report_weekly", title, text)

    async def generate_monthly(self):
        today          = date.today()
        first_of_month = today.replace(day=1)
        since          = (first_of_month - timedelta(days=1)).replace(day=1)
        until          = first_of_month - timedelta(days=1)
        log.info(f"월간 리포트: {since} ~ {until}")

        sub_reports = await self._fetch_sub_reports("report_weekly", since, until)
        data        = await self._collect_data(since, until, "monthly")

        if sub_reports:
            narrative = await self._synthesis(
                sub_reports,
                "4개 주간 리포트에서 이달 패턴과 연결고리를 4~5문장으로 해석해요.\n"
                "주간 단위에서는 안 보이던 이달의 구조적 변화·테마 이동에 집중해요.\n"
                "단순 주별 요약 나열 금지. 300자 이내.",
                300,
            )
        else:
            ctx = (
                f"=== {since.strftime('%Y년 %m월')} 매크로 변화 ===\n"
                f"{_render_macro_block(data.get('macro_start') or {}, data.get('macro_end') or {})}\n\n"
                f"=== 메르 관심 토픽 ===\n"
                f"{_render_topics_block(data.get('topics') or [])}\n\n"
                f"=== 많이 언급된 기업/자산 ===\n"
                f"{_render_entities_block(data.get('top_entities') or [], 10)}"
            )
            narrative = await self._commentary(
                ctx,
                "이달 시장을 4~5문장 산문으로 해석해요.\n"
                "토픽 간 연결 의미 포함, 인과관계·구조적 변화 중심, 나열 금지. 300자 이내.",
                300,
            )

        topics   = data.get("topics") or []
        entities = data.get("top_entities") or []

        header = f"*📅 {since.strftime('%Y년 %m월')} 시장 리포트*"
        msg1   = (
            f"{header}\n"
            f"_{since.strftime('%Y.%m.%d')} ~ {until.strftime('%Y.%m.%d')}_\n\n"
            f"{narrative}"
        )

        lines2 = [f"*이달 메르 관심 토픽*\n{_topic_line(topics, 5)}"]
        if entities:
            top5 = " · ".join(f"{e['entity']}({e['cnt']})" for e in entities[:5])
            lines2.append(f"\n*이달 많이 언급된 기업/자산*\n{top5}")
        msg2 = "\n".join(lines2)

        await self._send_parts("tier2", [msg1, msg2])
        await self._save("report_monthly", header, f"{msg1}\n\n{msg2}")

    async def generate_quarterly(self):
        today    = date.today()
        q        = (today.month - 1) // 3
        q_starts = [date(today.year, m, 1) for m in [1, 4, 7, 10]]
        if q == 0:
            since = date(today.year - 1, 10, 1)
            until = date(today.year, 1, 1) - timedelta(days=1)
        else:
            since = q_starts[q - 1]
            until = q_starts[q] - timedelta(days=1)
        log.info(f"분기 리포트: {since} ~ {until}")

        sub_reports = await self._fetch_sub_reports("report_monthly", since, until)
        data        = await self._collect_data(since, until, "quarterly")

        if sub_reports:
            synthesis = await self._synthesis(
                sub_reports,
                "3개 월간 리포트에서 이번 분기의 흐름 이동과 구조적 변화를 5~6문장으로 해석해요.\n"
                "월간 단위에서는 안 보이던 분기 레벨 패턴, 관심사 이동, 인과 연결에 집중해요.\n"
                "엇갈리는 지표를 반드시 짚어요 — 예: '수출 호조인데 환율이 오른 건 금리차 자본 유출', "
                "'VIX 안정인데 KOSPI 하락은 외국인 이탈 신호'.\n"
                "수치를 해석 문장 안에 자연스럽게 써요. 단순 월별 요약 나열 금지. 400자 이내.",
                400,
            )
        else:
            ctx = (
                f"=== 분기 매크로 변화 ===\n"
                f"{_render_macro_block(data.get('macro_start') or {}, data.get('macro_end') or {})}\n\n"
                f"=== 메르 관심 토픽 ===\n"
                f"{_render_topics_block(data.get('topics') or [])}"
            )
            synthesis = await self._commentary(
                ctx,
                "이번 분기 지표 간 연결 관계와 구조적 변화를 5~6문장으로 해석해요.\n"
                "엇갈리는 지표를 반드시 짚어요 — 예: '수출 호조인데 환율이 오른 건 금리차 자본 유출'.\n"
                "수치를 해석 문장 안에 자연스럽게 써요. 400자 이내.",
                400,
            )

        topics   = data.get("topics") or []
        entities = data.get("top_entities") or []
        preds    = data.get("predictions") or []

        title = "*📈 분기 시장 리포트*"
        msg1  = (
            f"{title}\n"
            f"_{since.strftime('%Y.%m.%d')} ~ {until.strftime('%Y.%m.%d')}_\n\n"
            f"{synthesis}"
        )

        lines2 = [f"*이번 분기 메르 관심 토픽*\n{_topic_line(topics, 6)}"]
        if entities:
            top5 = " · ".join(f"{e['entity']}({e['cnt']})" for e in entities[:5])
            lines2.append(f"\n*많이 언급된 기업/자산*\n{top5}")
        if preds:
            correct = sum(1 for p in preds if p.get("is_correct"))
            lines2.append(f"\n*예측 적중률* {correct}/{len(preds)}")
        msg2 = "\n".join(lines2)

        await self._send_parts("tier2", [msg1, msg2])
        await self._save("report_quarterly", title, f"{msg1}\n\n{msg2}")

    async def generate_annual(self):
        today = date.today()
        since = date(today.year - 1, 1, 1)
        until = date(today.year - 1, 12, 31)
        log.info(f"연간 리포트: {since} ~ {until}")

        sub_reports = await self._fetch_sub_reports("report_quarterly", since, until)
        data        = await self._collect_data(since, until, "annual")

        if sub_reports:
            # Claude 호출 1: 연간 흐름 분석
            macro_commentary = await self._synthesis(
                sub_reports,
                "4개 분기 리포트에서 한 해의 흐름·전환점·구조적 변화를 4~5문장으로 해석해요.\n"
                "분기별 단순 나열 금지. 연간 레벨에서만 보이는 패턴·인과 관계 중심.\n"
                "엇갈리는 지표(예: 금리 인하인데 환율 상승, 수출 호조인데 주가 부진)를 반드시 짚어요.\n"
                "수치를 해석 문장 안에 자연스럽게 써요. 300자 이내.",
                300,
            )
            # Claude 호출 2: 총평 & 전망
            outlook_commentary = await self._synthesis(
                sub_reports,
                "한 해의 핵심 키워드 2개로 총평하고, 내년 주목해야 할 테마 2~3개를 구체적 근거와 함께 제시해요.\n"
                "근거는 하위 리포트 내용에서만. 300자 이내.",
                300,
            )
        else:
            ctx = (
                f"=== 연간 매크로 변화 ===\n"
                f"{_render_macro_block(data.get('macro_start') or {}, data.get('macro_end') or {})}\n\n"
                f"=== 메르 관심 토픽 ===\n"
                f"{_render_topics_block(data.get('topics') or [])}\n\n"
                f"=== 기업/자산 언급 ===\n"
                f"{_render_entities_block(data.get('top_entities') or [], 10)}"
            )
            macro_commentary = await self._commentary(
                ctx,
                "연간 지표 간 상충·연결 신호를 4~5문장으로. 인과 관계 중심. 300자 이내.",
                300,
            )
            outlook_commentary = await self._commentary(
                ctx,
                "한 해의 핵심 키워드 2개로 총평, 내년 주목 테마 2~3개 제시. 300자 이내.",
                300,
            )

        topics   = data.get("topics") or []
        entities = data.get("top_entities") or []
        preds    = data.get("predictions") or []

        title = "*🗓 연간 시장 리포트*"
        msg1  = (
            f"{title}\n"
            f"_{since.strftime('%Y.%m.%d')} ~ {until.strftime('%Y.%m.%d')}_\n\n"
            f"{macro_commentary}"
        )
        msg2  = (
            f"*내년 전망*\n\n"
            f"{outlook_commentary}\n\n"
            f"*올해 메르 관심 토픽*\n"
            f"{_topic_line(topics, 7)}"
        )
        lines3 = []
        if entities:
            lines3.append(
                "*기업/자산 언급 TOP10*\n"
                + "\n".join(f"{i}. {e['entity']} ({e['cnt']}회)" for i, e in enumerate(entities[:10], 1))
            )
        if preds:
            correct = sum(1 for p in preds if p.get("is_correct"))
            lines3.append(f"*예측 적중률* {correct}/{len(preds)}")
        msg3 = "\n\n".join(lines3) if lines3 else ""

        parts = [p for p in [msg1, msg2, msg3] if p.strip()]
        await self._send_parts("tier2", parts)
        await self._save("report_annual", title, "\n\n".join(parts))

    # ─── 데이터 수집 ──────────────────────────────────────────────────────────

    async def _collect_data(self, since: date, until: date, rtype: str) -> dict:
        data: dict = {}

        data["macro_start"] = await self._macro_snapshot(since)
        data["macro_end"]   = await self._macro_snapshot(until)

        events = await self.conn.fetch("""
            SELECT e.title, e.event_type, e.event_date,
                   aa.analysis_text
            FROM events e
            JOIN auto_analyses aa ON aa.event_id = e.id
            WHERE e.event_date::date BETWEEN $1 AND $2
            ORDER BY e.event_date DESC
            LIMIT 15
        """, since, until)
        data["events"] = [dict(r) for r in events]

        if rtype in ("monthly", "quarterly", "annual"):
            trade = await self.conn.fetch("""
                SELECT ym, export_total, import_total, trade_balance
                FROM trade_monthly
                WHERE ym BETWEEN $1 AND $2
                ORDER BY ym
            """, since.strftime("%Y-%m"), until.strftime("%Y-%m"))
            if trade:
                data["trade"] = [dict(r) for r in trade]
            else:
                fallback = await self.conn.fetch("""
                    SELECT ym, export_total, import_total, trade_balance
                    FROM trade_monthly
                    ORDER BY ym DESC LIMIT 3
                """)
                data["trade"] = [dict(r) for r in reversed(fallback)]
                data["trade_note"] = f"{until.strftime('%Y-%m')} 발표 전 — 최근 데이터"

        topic_rows = await self.conn.fetch("""
            SELECT mi.structured_data->>'applicable_domain' AS topic,
                   COUNT(*) as cnt
            FROM mer_insights mi
            JOIN mer_posts mp ON mi.post_id = mp.id
            WHERE mp.date BETWEEN $1 AND $2
              AND mi.structured_data->>'applicable_domain' IS NOT NULL
              AND mi.insight_type IN ('rule','macro_view')
            GROUP BY topic
            ORDER BY cnt DESC
            LIMIT 10
        """, since, until)
        data["topics"] = [dict(r) for r in topic_rows]

        if rtype in ("monthly", "quarterly", "annual"):
            entity_limit = 5 if rtype == "monthly" else 20
            entity_rows = await self.conn.fetch("""
                SELECT mi.structured_data->>'entity_name' AS entity,
                       COUNT(*) as cnt
                FROM mer_insights mi
                JOIN mer_posts mp ON mi.post_id = mp.id
                WHERE mp.date BETWEEN $1 AND $2
                  AND mi.structured_data->>'entity_name' IS NOT NULL
                  AND mi.insight_type = 'evaluation'
                GROUP BY entity
                ORDER BY cnt DESC
                LIMIT $3
            """, since, until, entity_limit)
            data["top_entities"] = [dict(r) for r in entity_rows]

        if rtype in ("quarterly", "annual"):
            predictions = await self.conn.fetch("""
                SELECT prediction_text, predicted_direction,
                       target_asset, is_correct, actual_outcome
                FROM mer_predictions
                WHERE verification_date BETWEEN $1 AND $2
                  AND is_correct IS NOT NULL
                LIMIT 20
            """, since, until)
            data["predictions"] = [dict(r) for r in predictions]

        return data

    async def _macro_snapshot(self, target: date) -> dict:
        row = await self.conn.fetchrow("""
            SELECT date, kospi, kosdaq, usd_krw, us_10y,
                   kr_base_rate, fed_funds_rate, wti, btc_usd, vix,
                   us_cpi_yoy, us_unemployment
            FROM macro_daily
            WHERE date <= $1
              AND kospi IS NOT NULL AND kospi > 0
            ORDER BY date DESC LIMIT 1
        """, target)
        return dict(row) if row else {}

    # ─── Claude 호출 ──────────────────────────────────────────────────────────

    async def _commentary(self, data_str: str, instruction: str, max_chars: int) -> str:
        """[DATA] 블록 → 슬롯 채우기 (COMMENTARY_SYSTEM)."""
        user_msg = (
            f"[DATA]\n{data_str}\n\n"
            f"[COMMENTARY 작성 지시]\n{instruction}"
        )
        resp = await client.messages.create(
            model=MODEL_SONNET,
            max_tokens=max_chars * 2,
            system=_COMMENTARY_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = next((b.text for b in resp.content if isinstance(b, anthropic.types.TextBlock)), "")
        return text.strip()

    async def _synthesis(self, sub_reports: list[str], instruction: str, max_chars: int) -> str:
        """하위 리포트 목록 → 상위 관점 해석 (SYNTHESIS_SYSTEM)."""
        if not sub_reports:
            return "(하위 리포트 없음)"
        combined = "\n\n".join(sub_reports)
        user_msg = (
            f"[하위 리포트]\n{combined}\n\n"
            f"[작성 지시]\n{instruction}"
        )
        resp = await client.messages.create(
            model=MODEL_SONNET,
            max_tokens=max_chars * 2,
            system=_SYNTHESIS_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = next((b.text for b in resp.content if isinstance(b, anthropic.types.TextBlock)), "")
        return text.strip()

    # ─── 발송 / 저장 ──────────────────────────────────────────────────────────

    async def _fetch_sub_reports(
        self, event_type: str, since: date, until: date, limit: int = 10
    ) -> list[str]:
        """DB에서 저장된 하위 리포트 조회."""
        rows = await self.conn.fetch("""
            SELECT title, content
            FROM events
            WHERE event_type = $1
              AND event_date::date BETWEEN $2 AND $3
            ORDER BY event_date
            LIMIT $4
        """, event_type, since, until, limit)
        return [f"=== {r['title']} ===\n{r['content']}" for r in rows]

    async def _send_parts(self, channel: str, parts: list[str]):
        """파트 목록을 순서대로 Telegram 발송 (빈 파트 건너뜀)."""
        for part in parts:
            if part.strip():
                await self.telegram.send_raw(channel, part)

    async def _save(self, event_type: str, title: str, content: str):
        await self.conn.execute("""
            INSERT INTO events (event_type, source, title, content, event_date)
            VALUES ($1, 'report_generator', $2, $3, NOW())
        """, event_type, title, content)
