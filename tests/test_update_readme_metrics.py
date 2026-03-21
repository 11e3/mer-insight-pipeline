"""update_readme_metrics.py 테스트."""

import os
import tempfile

from scripts.ops.update_readme_metrics import update_readme

SAMPLE_METRICS = {
    "total": 6000,
    "correct": 550,
    "incorrect": 200,
    "verified": 750,
    "accuracy": 73.3,
    "verifiable_pending": 1200,
    "unverifiable": 2300,
    "future": 1000,
    "headlines": 60000,
}

KO_TEMPLATE = """\
### 지표

| | 값 |
|---|---|
| 추적 중인 예측 | 5,010건 |
| 자동 검증 완료 | 669건 (적중률 73.1%) |
| 뉴스 헤드라인 DB | 54,461건 |

### 현재 현황

| 상태 | 건수 |
|------|------|
| CORRECT | 489 |
| INCORRECT | 180 |
| 검증 가능 PENDING | 1,124 |
| 검증 불가 (모호/조건부) | 2,187 |
| 미래 대기 | 968 |

NC --> NHL[(news_headlines 54K)]

| 헤드라인 | 54,461건 |
"""

EN_TEMPLATE = """\
### Metrics

| | Value |
|---|---|
| Predictions tracked | 5,010 |
| Auto-verified | 669 (73.1% accuracy) |
| News headline DB | 54,461 |

### Current Status

| Status | Count |
|--------|-------|
| CORRECT | 489 |
| INCORRECT | 180 |
| Verifiable PENDING | 1,124 |
| Unverifiable (vague/conditional) | 2,187 |
| Future (awaiting expected_date) | 968 |

NC --> NHL[(news_headlines 54K)]

| Headlines | 54,461 (2022–2026) |
"""


def _write_temp(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_update_readme_ko():
    path = _write_temp(KO_TEMPLATE)
    try:
        changed = update_readme(path, SAMPLE_METRICS, lang="ko")
        assert changed is True

        with open(path, encoding="utf-8") as f:
            result = f.read()

        assert "6,000건" in result
        assert "750건 (적중률 73.3%)" in result
        assert "60,000건" in result
        assert "550" in result  # CORRECT
        assert "200" in result  # INCORRECT
        assert "1,200" in result  # verifiable_pending
        assert "2,300" in result  # unverifiable
        assert "1,000" in result  # future
        assert "news_headlines 60K" in result
    finally:
        os.unlink(path)


def test_update_readme_en():
    path = _write_temp(EN_TEMPLATE)
    try:
        changed = update_readme(path, SAMPLE_METRICS, lang="en")
        assert changed is True

        with open(path, encoding="utf-8") as f:
            result = f.read()

        assert "6,000" in result
        assert "750 (73.3% accuracy)" in result
        assert "60,000" in result
        assert "550" in result
        assert "200" in result
        assert "1,200" in result
        assert "2,300" in result
        assert "1,000" in result
        assert "news_headlines 60K" in result
    finally:
        os.unlink(path)


def test_update_readme_no_change():
    """Same metrics → no file write, returns False."""
    m = {
        "total": 5010,
        "correct": 489,
        "incorrect": 180,
        "verified": 669,
        "accuracy": 73.1,
        "verifiable_pending": 1124,
        "unverifiable": 2187,
        "future": 968,
        "headlines": 54461,
    }
    path = _write_temp(KO_TEMPLATE)
    try:
        changed = update_readme(path, m, lang="ko")
        assert changed is False
    finally:
        os.unlink(path)
