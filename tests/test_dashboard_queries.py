"""dashboard/queries.py 단위 테스트 — psycopg2 + streamlit 모킹."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

# Mock psycopg2 and streamlit before import of queries module
_mock_st = MagicMock()
_mock_st.cache_data = lambda **kwargs: (lambda fn: fn)

_mock_psycopg2 = MagicMock()
_mock_psycopg2_extras = MagicMock()
# Need RealDictCursor to be accessible
_mock_psycopg2.extras = _mock_psycopg2_extras

# Install mocks in sys.modules
sys.modules.setdefault("streamlit", _mock_st)
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.extras", _mock_psycopg2_extras)

# Force fresh import
for mod_name in list(sys.modules):
    if "src.dashboard" in mod_name:
        del sys.modules[mod_name]

from src.dashboard.queries import (
    _fetchone,
    _fetchall,
    load_prediction_summary,
    load_predictions_for_topics,
    load_monthly_trends,
    load_all_predictions,
)


def _mock_conn_and_cursor(rows, fetchone=False):
    mock_cursor = MagicMock()
    if fetchone:
        mock_cursor.fetchone.return_value = rows
    else:
        mock_cursor.fetchall.return_value = rows
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


def test_fetchone():
    row = {"total": 100, "correct": 50}
    mock_conn = _mock_conn_and_cursor(row, fetchone=True)

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        result = _fetchone("SELECT COUNT(*) FROM test")

    assert result == row
    mock_conn.close.assert_called_once()


def test_fetchall():
    rows = [{"id": 1}, {"id": 2}]
    mock_conn = _mock_conn_and_cursor(rows)

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        result = _fetchall("SELECT * FROM test")

    assert len(result) == 2
    mock_conn.close.assert_called_once()


def test_load_prediction_summary():
    summary = {"total": 200, "correct": 80, "incorrect": 30, "pending": 70, "skipped": 20}
    mock_conn = _mock_conn_and_cursor(summary, fetchone=True)

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        result = load_prediction_summary()

    assert result["total"] == 200


def test_load_predictions_for_topics():
    rows = [
        {"prediction_text": "금리 인하", "is_correct": True},
        {"prediction_text": "환율 상승", "is_correct": False},
    ]
    mock_conn = _mock_conn_and_cursor(rows)

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        result = load_predictions_for_topics()

    assert len(result) == 2


def test_load_monthly_trends():
    rows = [
        {"month": "2024-01", "total": 10, "verified": 8, "correct": 5},
        {"month": "2024-02", "total": 15, "verified": 12, "correct": 9},
    ]
    mock_conn = _mock_conn_and_cursor(rows)

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        result = load_monthly_trends()

    assert len(result) == 2


def test_load_all_predictions():
    rows = [{"prediction_date": "2024-01-01", "prediction_text": "코스피 상승"}]
    mock_conn = _mock_conn_and_cursor(rows)

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        result = load_all_predictions()

    assert len(result) == 1


def test_fetchone_closes_on_error():
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = Exception("SQL error")
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("src.dashboard.queries._get_conn", return_value=mock_conn):
        try:
            _fetchone("BAD SQL")
        except Exception:
            pass

    mock_conn.close.assert_called_once()
