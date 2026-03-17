"""date_parser 단위 테스트."""

from datetime import date, datetime

from src.collect.date_parser import parse_mer_date

BASE = datetime(2025, 3, 15, 12, 0, 0)


# ── 절대시간 ──────────────────────────────────────────────────────────────────

def test_absolute_dot_with_time():
    assert parse_mer_date("2024. 9. 4. 0:10") == date(2024, 9, 4)


def test_absolute_dot_no_time():
    assert parse_mer_date("2024. 9. 4.") == date(2024, 9, 4)


def test_absolute_dash():
    assert parse_mer_date("2024-09-04") == date(2024, 9, 4)


def test_absolute_slash():
    assert parse_mer_date("2024/09/04") == date(2024, 9, 4)


def test_absolute_dot_padded():
    assert parse_mer_date("2024.09.04") == date(2024, 9, 4)


# ── 상대시간 ──────────────────────────────────────────────────────────────────

def test_relative_minutes():
    assert parse_mer_date("30분 전", scraped_at=BASE) == date(2025, 3, 15)


def test_relative_hours():
    assert parse_mer_date("9시간 전", scraped_at=BASE) == date(2025, 3, 15)


def test_relative_days():
    assert parse_mer_date("3일 전", scraped_at=BASE) == date(2025, 3, 12)


def test_relative_weeks():
    assert parse_mer_date("1주 전", scraped_at=BASE) == date(2025, 3, 8)


def test_relative_months():
    assert parse_mer_date("2달 전", scraped_at=BASE) == date(2025, 1, 14)


# ── 엣지 케이스 ──────────────────────────────────────────────────────────────

def test_empty_string():
    assert parse_mer_date("") is None


def test_none_input():
    assert parse_mer_date(None) is None


def test_whitespace_only():
    assert parse_mer_date("   ") is None


def test_unrecognized_format():
    assert parse_mer_date("어제") is None
