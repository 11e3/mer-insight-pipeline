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
    "원자재": ["유가", "WTI", "원유", "원자재", "금값", "구리", "천연가스", "LNG"],
    "금리": ["금리", "기준금리", "금통위", "FOMC", "rate", "금리인하", "금리인상", "기금금리"],
    "환율": ["환율", "원/달러", "USD/KRW", "원달러", "달러/원", "엔화", "위안", "달러 약세", "달러 강세"],
    "부동산": ["부동산", "아파트", "주택", "전세", "매매", "분양", "집값"],
    "주식": ["주식", "KOSPI", "코스피", "코스닥", "삼성", "SK", "반도체", "하이닉스", "주가"],
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
def load_recent_verified(limit: int = 20):
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
            WHERE mp.is_correct IS NOT NULL
            ORDER BY mp.verification_date DESC
            LIMIT $1
        """, limit)
        await conn.close()
        return [dict(r) for r in rows]
    return run_async(_())


# ─── 레이아웃 ────────────────────────────────────────────────────────────────

st.title("예측 검증 대시보드")
st.caption("메르 블로그 예측 → Claude Haiku 자동 검증 결과")

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
    ))

    fig_trend.add_trace(go.Scatter(
        x=df_m["month"], y=df_m["accuracy"],
        name="적중률 (%)", yaxis="y2",
        mode="lines+markers", marker_color="#e74c3c",
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

# ─── 4. 최근 검증된 예측 ─────────────────────────────────────────────────────

st.subheader("최근 검증된 예측")

recent_limit = st.slider("표시 건수", 10, 50, 20)
recent = load_recent_verified(recent_limit)

if recent:
    for r in recent:
        verdict = "CORRECT" if r["is_correct"] else "INCORRECT"
        icon = "✅" if r["is_correct"] else "❌"
        pred_date = r["prediction_date"].strftime("%Y-%m-%d") if r["prediction_date"] else ""
        asset = r["target_asset"] or ""
        short = (r["prediction_text"] or "")[:50]
        label = f"{icon} [{pred_date}] {asset} — {short}"

        with st.expander(label):
            st.markdown(f"**예측 내용**")
            pred_text = r["prediction_text"] or ""
            if r.get("post_url"):
                st.markdown(f"{pred_text}\n\n🔗 [원글 보기]({r['post_url']})")
            else:
                st.write(pred_text)

            st.markdown(f"**판정: {verdict}** | 방향: `{r['predicted_direction']}` | "
                        f"검증일: {r['verification_date']}")

            outcome = r.get("actual_outcome") or ""
            if outcome:
                st.markdown(f"**근거:** {outcome}")
else:
    st.info("검증된 예측이 없습니다.")
