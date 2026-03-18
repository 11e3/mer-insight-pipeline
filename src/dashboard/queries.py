"""대시보드 DB 쿼리 함수들."""

import asyncio
import os

import asyncpg
import streamlit as st


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
def load_prediction_summary():
    async def _():
        conn = await _get_conn()
        try:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct,
                    COUNT(*) FILTER (WHERE is_correct = FALSE) AS incorrect,
                    COUNT(*) FILTER (WHERE is_correct IS NULL AND skipped_at IS NULL) AS pending,
                    COUNT(*) FILTER (WHERE skipped_at IS NOT NULL) AS skipped
                FROM mer_predictions
            """)
            return dict(row)
        finally:
            await conn.close()
    return run_async(_())


@st.cache_data(ttl=60)
def load_predictions_for_topics():
    async def _():
        conn = await _get_conn()
        try:
            rows = await conn.fetch("""
                SELECT prediction_text, is_correct
                FROM mer_predictions
                WHERE skipped_at IS NULL
            """)
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    return run_async(_())


@st.cache_data(ttl=60)
def load_monthly_trends():
    async def _():
        conn = await _get_conn()
        try:
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
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    return run_async(_())


@st.cache_data(ttl=60)
def load_all_predictions():
    async def _():
        conn = await _get_conn()
        try:
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
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    return run_async(_())
