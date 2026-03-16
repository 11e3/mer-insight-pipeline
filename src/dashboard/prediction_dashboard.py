"""
Prediction Accuracy Dashboard: 예측 적중률 Streamlit 대시보드

Usage:
    streamlit run src/dashboard/prediction_dashboard.py
"""

import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (streamlit run 시 cwd가 달라질 수 있음)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncpg
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Mer Pipeline — Prediction Accuracy", layout="wide")


# ─── 주제 분류 ────────────────────────────────────────────────────────────────

TOPIC_KEYWORDS = {
    "에너지/원유": ["원유", "WTI", "LNG", "천연가스", "에너지", "셰일", "원자력", "우라늄", "유가", "OPEC"],
    "금리/매크로": ["금리", "기준금리", "금통위", "FOMC", "금리인하", "금리인상", "인플레", "CPI", "연준", "기금금리"],
    "환율": ["환율", "원/달러", "USD/KRW", "원달러", "달러/원", "엔화", "위안", "달러 약세", "달러 강세"],
    "부동산": ["부동산", "아파트", "주택", "전세", "매매", "분양", "집값", "월세", "임대"],
    "주식/금융": ["주식", "KOSPI", "코스피", "코스닥", "주가", "사모", "펀드", "IPO", "증시", "배당"],
    "AI/반도체": ["AI", "반도체", "엔비디아", "딥시크", "데이터센터", "하이닉스", "삼성전자", "클라우드"],
    "관세/무역": ["관세", "무역", "수입", "수출", "상호관세", "덤핑", "WTO", "232조", "301조"],
    "트럼프/미국": ["트럼프", "파월", "백악관", "공화당", "민주당", "중간선거", "바이든"],
    "중동/지정학": ["호르무즈", "이란", "중동", "카타르", "사우디", "이스라엘", "하마스", "네타냐후", "우크라이나", "러시아"],
    "조선/해운": ["조선", "해운", "HD현대", "선박", "한화오션", "삼성중공업"],
    "방산/안보": ["방산", "천궁", "무인", "안두릴", "방위", "미사일", "NATO", "잠수함"],
    "원자재/금속": ["금값", "은값", "구리", "원자재", "다이아몬드", "헬륨", "금속", "리튬", "희토류"],
    "식품/농업": ["농산물", "곡물", "식품", "수산물", "비료", "요소"],
    "의료/바이오": ["오가노이드", "의학", "의료", "바이오", "제약"],
    "북한": ["북한", "김정은", "핵"],
    "중국": ["중국", "시진핑", "대만", "위구르", "홍콩"],
}


def classify_topic(text: str) -> str:
    if not text:
        return "기타"
    text_lower = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw.lower() in text_lower for kw in keywords):
            return topic
    return "기타"


# ─── DB 연결 (캐시) ──────────────────────────────────────────────────────────

@st.cache_resource
def get_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def run_async(coro):
    loop = get_event_loop()
    return loop.run_until_complete(coro)


async def _get_conn():
    return await asyncpg.connect(os.environ["DATABASE_URL"])


# ─── 데이터 로더 ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_prediction_summary():
    async def _():
        conn = await _get_conn()
        row = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct,
                COUNT(*) FILTER (WHERE is_correct = FALSE) AS incorrect,
                COUNT(*) FILTER (WHERE is_correct IS NULL AND skipped_at IS NULL) AS pending,
                COUNT(*) FILTER (WHERE skipped_at IS NOT NULL) AS skipped
            FROM mer_predictions
        """)
        await conn.close()
        return dict(row)
    return run_async(_())


@st.cache_data(ttl=60)
def load_predictions_for_topics():
    async def _():
        conn = await _get_conn()
        rows = await conn.fetch("""
            SELECT prediction_text, is_correct
            FROM mer_predictions
            WHERE skipped_at IS NULL
        """)
        await conn.close()
        return [dict(r) for r in rows]
    return run_async(_())


@st.cache_data(ttl=60)
def load_monthly_trends():
    async def _():
        conn = await _get_conn()
        rows = await conn.fetch("""
            SELECT
                to_char(prediction_date, 'YYYY-MM') AS month,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_correct IS NOT NULL) AS verified,
                COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct
            FROM mer_predictions
            WHERE skipped_at IS NULL AND prediction_date IS NOT NULL
            GROUP BY to_char(prediction_date, 'YYYY-MM')
            ORDER BY month
        """)
        await conn.close()
        return [dict(r) for r in rows]
    return run_async(_())


@st.cache_data(ttl=60)
def load_all_predictions():
    async def _():
        conn = await _get_conn()
        rows = await conn.fetch("""
            SELECT
                mp.prediction_date, mp.prediction_text, mp.predicted_direction,
                mp.target_asset, mp.is_correct, mp.actual_outcome, mp.verification_date,
                p.url AS post_url
            FROM mer_predictions mp
            LEFT JOIN mer_insights mi ON mi.id = mp.insight_id
            LEFT JOIN mer_posts p ON p.id = mi.post_id
            WHERE mp.skipped_at IS NULL
            ORDER BY mp.prediction_date DESC
        """)
        await conn.close()
        return [dict(r) for r in rows]
    return run_async(_())


# ─── 레이아웃 ────────────────────────────────────────────────────────────────

st.title("예측 검증 대시보드")
st.caption("메르 블로그 예측 → Claude 자동 검증 결과")

# ─── 1. 전체 요약 ────────────────────────────────────────────────────────────

st.subheader("전체 요약")

summary = load_prediction_summary()

col1, col2, col3, col4 = st.columns(4)
col1.metric("총 예측 수", f"{summary['total']:,}건")
col2.metric("CORRECT", f"{summary['correct']:,}건")
col3.metric("INCORRECT", f"{summary['incorrect']:,}건")
col4.metric("PENDING", f"{summary['pending']:,}건")

verified = summary["correct"] + summary["incorrect"]
if verified > 0:
    accuracy = summary["correct"] / verified * 100
    st.metric("검증 완료 적중률", f"{accuracy:.1f}%",
              help=f"검증 완료 {verified:,}건 중 CORRECT 비율")

# 파이 차트
pie_data = pd.DataFrame({
    "verdict": ["CORRECT", "INCORRECT", "PENDING"],
    "count": [summary["correct"], summary["incorrect"], summary["pending"]],
})
pie_data = pie_data[pie_data["count"] > 0]

if not pie_data.empty:
    fig_pie = px.pie(
        pie_data, values="count", names="verdict",
        color="verdict",
        color_discrete_map={"CORRECT": "#2ecc71", "INCORRECT": "#e74c3c", "PENDING": "#95a5a6"},
        title="판정 분포",
    )
    fig_pie.update_traces(textinfo="percent+value")
    st.plotly_chart(fig_pie, use_container_width=True)

st.divider()

# ─── 2. 주제별 적중률 ────────────────────────────────────────────────────────

st.subheader("주제별 적중률")

preds = load_predictions_for_topics()

if preds:
    for p in preds:
        p["topic"] = classify_topic(p["prediction_text"])
        if p["is_correct"] is True:
            p["verdict"] = "CORRECT"
        elif p["is_correct"] is False:
            p["verdict"] = "INCORRECT"
        else:
            p["verdict"] = "PENDING"

    df_topic = pd.DataFrame(preds)

    # 주제별 집계 (검증 완료만)
    df_verified = df_topic[df_topic["verdict"] != "PENDING"]
    if not df_verified.empty:
        topic_stats = df_verified.groupby(["topic", "verdict"]).size().reset_index(name="count")
        fig_bar = px.bar(
            topic_stats, x="topic", y="count", color="verdict",
            barmode="group",
            color_discrete_map={"CORRECT": "#2ecc71", "INCORRECT": "#e74c3c"},
            title="주제별 검증 결과",
            labels={"topic": "주제", "count": "건수", "verdict": "판정"},
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        # 주제별 정확도 테이블
        topic_acc = df_verified.groupby("topic").apply(
            lambda g: pd.Series({
                "total": len(g),
                "correct": (g["verdict"] == "CORRECT").sum(),
                "accuracy": f"{(g['verdict'] == 'CORRECT').mean() * 100:.1f}%",
            })
        ).reset_index()
        st.dataframe(topic_acc, use_container_width=True, hide_index=True)
    else:
        st.info("검증 완료된 예측이 없습니다.")
else:
    st.info("예측 데이터가 없습니다.")

st.divider()

# ─── 3. 시간별 추이 ──────────────────────────────────────────────────────────

st.subheader("월별 추이")

monthly = load_monthly_trends()

if monthly:
    df_m = pd.DataFrame(monthly)
    df_m["accuracy"] = df_m.apply(
        lambda r: r["correct"] / r["verified"] * 100 if r["verified"] > 0 else None, axis=1
    )

    fig_trend = go.Figure()

    fig_trend.add_trace(go.Bar(
        x=df_m["month"], y=df_m["total"],
        name="예측 수", marker_color="#3498db", opacity=0.7,
        hovertemplate="월: %{x}<br>예측 수: %{y}건<extra></extra>",
    ))

    fig_trend.add_trace(go.Scatter(
        x=df_m["month"], y=df_m["accuracy"],
        name="적중률 (%)", yaxis="y2",
        mode="lines+markers", marker_color="#e74c3c",
        hovertemplate="월: %{x}<br>적중률: %{y:.1f}%<extra></extra>",
    ))

    fig_trend.update_layout(
        title="월별 예측 수 & 적중률",
        xaxis=dict(title="월", type="category"),
        yaxis=dict(title="예측 수", side="left"),
        yaxis2=dict(title="적중률 (%)", side="right", overlaying="y", range=[0, 100]),
        legend=dict(x=0.01, y=0.99),
    )

    st.plotly_chart(fig_trend, use_container_width=True)
else:
    st.info("월별 데이터가 없습니다.")

st.divider()

# ─── 4. 전체 예측 목록 ────────────────────────────────────────────────────────

st.subheader("전체 예측 목록")

all_preds = load_all_predictions()

if all_preds:
    # 필터
    col_f1, col_f2, col_f3 = st.columns(3)
    verdict_filter = col_f1.selectbox("판정", ["전체", "CORRECT", "INCORRECT", "PENDING"])
    topics = sorted({classify_topic(p["prediction_text"]) for p in all_preds})
    topic_filter = col_f2.selectbox("주제", ["전체"] + topics)
    direction_filter = col_f3.selectbox("방향", ["전체", "up", "down", "neutral"])

    filtered = all_preds
    if verdict_filter != "전체":
        if verdict_filter == "PENDING":
            filtered = [p for p in filtered if p["is_correct"] is None]
        elif verdict_filter == "CORRECT":
            filtered = [p for p in filtered if p["is_correct"] is True]
        else:
            filtered = [p for p in filtered if p["is_correct"] is False]
    if topic_filter != "전체":
        filtered = [p for p in filtered if classify_topic(p["prediction_text"]) == topic_filter]
    if direction_filter != "전체":
        filtered = [p for p in filtered if p["predicted_direction"] == direction_filter]

    # 페이지네이션
    PAGE_SIZE = 20
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)

    if "pred_page" not in st.session_state:
        st.session_state.pred_page = 1
    # 필터 변경 시 페이지 리셋
    if st.session_state.pred_page > total_pages:
        st.session_state.pred_page = 1

    page = st.session_state.pred_page

    st.caption(f"{len(filtered):,}건 표시 (전체 {len(all_preds):,}건) — 페이지 {page}/{total_pages}")

    # 상단 페이지 네비게이션
    nav_cols = st.columns([1, 1, 1, 1, 1, 3])
    if nav_cols[0].button("⏮ 처음", disabled=page <= 1):
        st.session_state.pred_page = 1
        st.rerun()
    if nav_cols[1].button("◀ 이전", disabled=page <= 1):
        st.session_state.pred_page = page - 1
        st.rerun()
    if nav_cols[2].button("다음 ▶", disabled=page >= total_pages):
        st.session_state.pred_page = page + 1
        st.rerun()
    if nav_cols[3].button("마지막 ⏭", disabled=page >= total_pages):
        st.session_state.pred_page = total_pages
        st.rerun()
    jump = nav_cols[4].selectbox(
        "이동", range(1, total_pages + 1), index=page - 1, label_visibility="collapsed",
    )
    if jump != page:
        st.session_state.pred_page = jump
        st.rerun()

    page_items = filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    for r in page_items:
        if r["is_correct"] is True:
            icon, verdict = "✅", "CORRECT"
        elif r["is_correct"] is False:
            icon, verdict = "❌", "INCORRECT"
        else:
            icon, verdict = "⏳", "PENDING"

        pred_date = r["prediction_date"].strftime("%Y-%m-%d") if r["prediction_date"] else ""
        asset = r["target_asset"] or ""
        short = (r["prediction_text"] or "")[:50]
        label = f"{icon} [{pred_date}] {asset} — {short}"

        with st.expander(label):
            pred_text = r["prediction_text"] or ""
            if r.get("post_url"):
                st.markdown(f"{pred_text}\n\n🔗 [원글 보기]({r['post_url']})")
            else:
                st.write(pred_text)

            parts = [f"**판정: {verdict}**", f"방향: `{r['predicted_direction']}`"]
            if r.get("verification_date"):
                parts.append(f"검증일: {r['verification_date']}")
            st.markdown(" | ".join(parts))

            outcome = r.get("actual_outcome") or ""
            if outcome:
                st.markdown(f"**근거:** {outcome}")
else:
    st.info("예측 데이터가 없습니다.")

# ─── 면책 문구 ────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "이 대시보드는 메르(ranto28) 블로그 포스트에서 AI(Claude)가 자동 추출한 예측을 "
    "AI(Claude)가 자동 검증한 결과를 보여줍니다. "
    "메르 본인의 공식 입장이 아니며, AI 추출·판정 과정에서 오류가 있을 수 있습니다. "
    "투자 판단의 근거로 사용하지 마세요."
)
