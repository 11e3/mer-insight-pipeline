"""
Observability Dashboard: Streamlit 대시보드

Usage:
    streamlit run src/dashboard/observability.py
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
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Mer Pipeline — Observability", layout="wide")


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


@st.cache_data(ttl=60)
def load_daily_cost(days: int):
    from src.observability.storage import daily_cost_summary
    async def _():
        conn = await _get_conn()
        data = await daily_cost_summary(conn, days)
        await conn.close()
        return data
    return run_async(_())


@st.cache_data(ttl=60)
def load_latency(days: int):
    from src.observability.storage import latency_summary
    async def _():
        conn = await _get_conn()
        data = await latency_summary(conn, days)
        await conn.close()
        return data
    return run_async(_())


@st.cache_data(ttl=60)
def load_error_rate(days: int):
    from src.observability.storage import error_rate
    async def _():
        conn = await _get_conn()
        data = await error_rate(conn, days)
        await conn.close()
        return data
    return run_async(_())


@st.cache_data(ttl=30)
def load_recent_traces():
    from src.observability.storage import recent_traces
    async def _():
        conn = await _get_conn()
        data = await recent_traces(conn, limit=50)
        await conn.close()
        return data
    return run_async(_())


def load_trace_spans(trace_id: str):
    from src.observability.storage import trace_spans
    async def _():
        conn = await _get_conn()
        data = await trace_spans(conn, trace_id)
        await conn.close()
        return data
    return run_async(_())


# ─── 레이아웃 ────────────────────────────────────────────────────────────────

st.title("🔍 Mer Pipeline — Observability")

days = st.sidebar.slider("조회 기간 (일)", 7, 90, 30)
st.sidebar.caption(f"기준: 최근 {days}일")

# ─── KPI 카드 ────────────────────────────────────────────────────────────────

cost_data = load_daily_cost(days)
err_data   = load_error_rate(days)

col1, col2, col3, col4 = st.columns(4)
total_cost = sum(r["cost"] for r in cost_data) if cost_data else 0.0
total_traces = sum(r["trace_count"] for r in cost_data) if cost_data else 0
col1.metric("총 API 비용", f"${total_cost:.3f}")
col2.metric("총 Trace 수", f"{total_traces:,}")
col3.metric("에러율", f"{err_data['rate']:.1%}")
col4.metric("에러 건수", f"{err_data['error_count']}")

st.divider()

# ─── 비용 추이 ────────────────────────────────────────────────────────────────

if cost_data:
    df_cost = pd.DataFrame(cost_data)
    fig = px.bar(df_cost, x="day", y="cost", title=f"일별 API 비용 (최근 {days}일)",
                 labels={"day": "날짜", "cost": "비용 (USD)"})
    st.plotly_chart(fig, use_container_width=True)

# ─── Latency ────────────────────────────────────────────────────────────────

lat_data = load_latency(days)
if lat_data:
    df_lat = pd.DataFrame(lat_data)
    fig2 = px.bar(df_lat, x="name", y=["avg_ms", "p95_ms"],
                  barmode="group", title="Span별 평균/P95 Latency (ms)",
                  labels={"name": "Span", "value": "ms"})
    st.plotly_chart(fig2, use_container_width=True)

# ─── 토큰 사용량 ─────────────────────────────────────────────────────────────

if cost_data:
    df_tok = pd.DataFrame(cost_data)
    fig3 = px.line(df_tok, x="day", y=["in_tokens", "out_tokens"],
                   title="일별 토큰 사용량",
                   labels={"day": "날짜", "value": "토큰"})
    st.plotly_chart(fig3, use_container_width=True)

st.divider()

# ─── 최근 Trace 목록 ─────────────────────────────────────────────────────────

st.subheader("최근 Trace")
traces = load_recent_traces()
if traces:
    df_t = pd.DataFrame(traces)
    df_t["start_time"] = pd.to_datetime(df_t["start_time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df_t["total_cost_usd"] = df_t["total_cost_usd"].apply(lambda x: f"${x:.4f}")
    st.dataframe(df_t, use_container_width=True, hide_index=True)

    # Trace 상세 드릴다운
    selected_id = st.text_input("Trace ID 입력 (상세 조회)")
    if selected_id:
        spans = load_trace_spans(selected_id)
        if spans:
            df_s = pd.DataFrame(spans)
            st.subheader(f"Trace {selected_id[:8]}… Spans")
            st.dataframe(df_s, use_container_width=True, hide_index=True)
        else:
            st.warning("해당 Trace ID의 span이 없어요.")
else:
    st.info("기록된 Trace가 없어요.")
