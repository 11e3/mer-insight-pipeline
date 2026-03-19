"""대시보드 DB 쿼리 함수들."""

import os

import psycopg2
import psycopg2.extras
import streamlit as st


def _get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _fetchone(query):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return dict(cur.fetchone())
    finally:
        conn.close()


def _fetchall(query):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@st.cache_data(ttl=60)
def load_prediction_summary():
    return _fetchone("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct,
            COUNT(*) FILTER (WHERE is_correct = FALSE) AS incorrect,
            COUNT(*) FILTER (WHERE is_correct IS NULL AND skipped_at IS NULL) AS pending,
            COUNT(*) FILTER (WHERE skipped_at IS NOT NULL) AS skipped
        FROM mer_predictions
    """)


@st.cache_data(ttl=60)
def load_predictions_for_topics():
    return _fetchall("""
        SELECT prediction_text, is_correct
        FROM mer_predictions
        WHERE skipped_at IS NULL
    """)


@st.cache_data(ttl=60)
def load_monthly_trends():
    return _fetchall("""
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


@st.cache_data(ttl=60)
def load_all_predictions():
    return _fetchall("""
        SELECT
            mp.prediction_date, mp.prediction_text, mp.predicted_direction,
            mp.target_asset, mp.is_correct, mp.actual_outcome, mp.source_url,
            mp.verification_date, p.url AS post_url
        FROM mer_predictions mp
        LEFT JOIN mer_insights mi ON mi.id = mp.insight_id
        LEFT JOIN mer_posts p ON p.id = mi.post_id
        WHERE mp.skipped_at IS NULL
        ORDER BY mp.prediction_date DESC
    """)
