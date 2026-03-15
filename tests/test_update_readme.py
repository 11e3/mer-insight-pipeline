"""
update_readme.py 단위 테스트 — 파일 I/O 없음.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.update_readme import (
    _kwarg,
    _fmt_cron,
    _DOW_EN,
    _DOW_KR,
    update_section,
    parse_jobs,
)


# ── _kwarg ────────────────────────────────────────────────────────────────────

def test_kwarg_quoted_double():
    block = 'hour="3,6,9,12", minute=0, id="test"'
    assert _kwarg(block, "hour") == "3,6,9,12"

def test_kwarg_quoted_single():
    block = "day_of_week='mon-fri', hour=8"
    assert _kwarg(block, "day_of_week") == "mon-fri"

def test_kwarg_unquoted_int():
    block = "hour=20, minute=0, id=\"report_daily\""
    assert _kwarg(block, "hour") == "20"

def test_kwarg_star_slash():
    block = 'minute="*/10", hour="8-18"'
    assert _kwarg(block, "minute") == "*/10"

def test_kwarg_missing():
    block = "hour=20, minute=0"
    assert _kwarg(block, "day_of_week") is None

def test_kwarg_last():
    block = 'day="last", hour=21'
    assert _kwarg(block, "day") == "last"


# ── _fmt_cron ─────────────────────────────────────────────────────────────────

def test_fmt_cron_simple_daily():
    block = "hour=21, minute=0"
    assert _fmt_cron(block, _DOW_EN) == "21:00"

def test_fmt_cron_weekly_en():
    block = "day_of_week='sun', hour=21, minute=0"
    result = _fmt_cron(block, _DOW_EN, lang="en")
    assert "Sun" in result
    assert "21:00" in result

def test_fmt_cron_weekly_kr():
    block = "day_of_week='sun', hour=21, minute=0"
    result = _fmt_cron(block, _DOW_KR, lang="kr")
    assert "매주 일요일" in result

def test_fmt_cron_monthly_last_en():
    block = "day='last', hour=21, minute=0"
    result = _fmt_cron(block, _DOW_EN, lang="en")
    assert "last day" in result

def test_fmt_cron_monthly_last_kr():
    block = "day='last', hour=21, minute=0"
    result = _fmt_cron(block, _DOW_KR, lang="kr")
    assert "말일" in result

def test_fmt_cron_month_day_en():
    block = "month=12, day=31, hour=21, minute=0"
    result = _fmt_cron(block, _DOW_EN, lang="en")
    assert "12/31" in result

def test_fmt_cron_month_day_kr():
    block = "month=12, day=31, hour=21, minute=0"
    result = _fmt_cron(block, _DOW_KR, lang="kr")
    assert "12월 31일" in result

def test_fmt_cron_interval_minutes():
    block = 'minute="*/10", hour="8-18"'
    result = _fmt_cron(block, _DOW_EN, lang="en")
    assert "every 10 min" in result

def test_fmt_cron_zero_padding():
    block = "hour=9, minute=5"
    result = _fmt_cron(block, _DOW_EN)
    assert "9:05" in result


# ── update_section ────────────────────────────────────────────────────────────

SAMPLE = """\
# 제목

<!-- AUTO:tech_stack -->
old content
<!-- END:tech_stack -->

## 다른 섹션
"""

def test_update_section_replaces_content():
    result = update_section(SAMPLE, "tech_stack", "new content")
    assert "new content" in result
    assert "old content" not in result

def test_update_section_preserves_markers():
    result = update_section(SAMPLE, "tech_stack", "new content")
    assert "<!-- AUTO:tech_stack -->" in result
    assert "<!-- END:tech_stack -->" in result

def test_update_section_missing_section(capsys):
    result = update_section(SAMPLE, "nonexistent", "new content")
    assert result == SAMPLE  # 변경 없음
    captured = capsys.readouterr()
    assert "WARNING" in captured.err

def test_update_section_no_change_when_same():
    content = "<!-- AUTO:jobs -->\ncurrent\n<!-- END:jobs -->"
    result = update_section(content, "jobs", "current")
    assert result == content


# ── parse_jobs ────────────────────────────────────────────────────────────────

SAMPLE_DISPATCHER = '''
    self.scheduler.add_job(
        self._check_mer_new_posts, "interval", minutes=5, id="mer"
    )
    self.scheduler.add_job(
        self._send_daily_report, "cron",
        hour=21, minute=0, id="report_daily"
    )
    self.scheduler.add_job(
        self._send_weekly_report, "cron",
        day_of_week="sun", hour=21, minute=0, id="report_weekly"
    )
'''

def test_parse_jobs_count():
    jobs = parse_jobs(SAMPLE_DISPATCHER)
    assert len(jobs) == 3

def test_parse_jobs_ids():
    jobs = parse_jobs(SAMPLE_DISPATCHER)
    ids = [j[0] for j in jobs]
    assert "mer" in ids
    assert "report_daily" in ids
    assert "report_weekly" in ids

def test_parse_jobs_interval_en():
    jobs = parse_jobs(SAMPLE_DISPATCHER)
    mer = next(j for j in jobs if j[0] == "mer")
    assert "every 5 min" in mer[1]

def test_parse_jobs_cron_weekly_kr():
    jobs = parse_jobs(SAMPLE_DISPATCHER)
    weekly = next(j for j in jobs if j[0] == "report_weekly")
    assert "매주 일요일" in weekly[2]
