"""대시보드 DB 쿼리 함수들."""

import os

import psycopg2
import psycopg2.extras
import psycopg2.pool
import streamlit as st


@st.cache_resource
def _get_pool():
    """커넥션 풀 생성 (Streamlit 앱 lifetime 동안 유지)."""
    return psycopg2.pool.SimpleConnectionPool(
        minconn=1, maxconn=5,
        dsn=os.environ["DATABASE_URL"],
    )


def _fetchone(query):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        pool.putconn(conn)


def _fetchall(query):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return [dict(r) for r in cur.fetchall()]
    finally:
        pool.putconn(conn)


@st.cache_data(ttl=60)
def load_prediction_summary():
    return _fetchone("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct,
            COUNT(*) FILTER (WHERE is_correct = FALSE) AS incorrect,
            COUNT(*) FILTER (WHERE is_correct IS NULL AND is_verifiable = true) AS verifiable_pending,
            COUNT(*) FILTER (WHERE is_correct IS NULL AND is_verifiable = false) AS unverifiable,
            COUNT(*) FILTER (WHERE is_correct IS NULL AND expected_date > CURRENT_DATE) AS future,
            COUNT(*) FILTER (WHERE is_correct IS NULL AND is_verifiable IS NULL AND expected_date IS NULL) AS unclassified
        FROM predictions
    """)


@st.cache_data(ttl=60)
def load_predictions_for_topics():
    return _fetchall("""
        SELECT prediction_text, is_correct
        FROM predictions
        WHERE is_verifiable IS NOT FALSE
    """)


@st.cache_data(ttl=60)
def load_monthly_trends():
    return _fetchall("""
        SELECT
            to_char(prediction_date, 'YYYY-MM') AS month,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE is_correct IS NOT NULL) AS verified,
            COUNT(*) FILTER (WHERE is_correct = TRUE) AS correct
        FROM predictions
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
            mp.verification_date, mp.is_verifiable, mp.expected_date,
            p.url AS post_url,
            COALESCE(s.name, 'mer_ranto28') AS source_name
        FROM predictions mp
        LEFT JOIN insights mi ON mi.id = mp.insight_id
        LEFT JOIN posts p ON p.id = mi.post_id
        LEFT JOIN sources s ON s.id = mp.source_id
        ORDER BY mp.prediction_date DESC
    """)


@st.cache_data(ttl=300)
def load_sources():
    return _fetchall("""
        SELECT name, source_type, platform, url, is_active,
            (SELECT COUNT(*) FROM predictions WHERE source_id = sources.id) AS prediction_count
        FROM sources
        ORDER BY name
    """)


@st.cache_data(ttl=60)
def load_leaderboard():
    return _fetchall("""
        SELECT
            COALESCE(s.name, 'mer_ranto28') AS source,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE mp.is_correct IS NOT NULL) AS verified,
            COUNT(*) FILTER (WHERE mp.is_correct = TRUE) AS correct,
            COUNT(*) FILTER (WHERE mp.is_correct = FALSE) AS incorrect,
            COUNT(*) FILTER (WHERE mp.is_correct IS NULL AND mp.is_verifiable = true) AS pending,
            COUNT(*) FILTER (WHERE mp.is_correct IS NULL AND mp.expected_date > CURRENT_DATE) AS future
        FROM predictions mp
        LEFT JOIN sources s ON s.id = mp.source_id
        GROUP BY s.name
        ORDER BY correct DESC
    """)
