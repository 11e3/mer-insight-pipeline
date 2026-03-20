"""collect/mer_monitor.py 단위 테스트 — 네트워크/DB 의존성 없음."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.collect.mer_monitor import MerMonitor
from src.collect.source_protocol import CollectedPost


def _make_monitor():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)  # source_id
    return MerMonitor(conn), conn


# ─── _get_recent_log_nos ─────────────────────────────────────────────────────

def test_get_recent_log_nos_rss_success():
    """RSS 파싱 성공 시 log_no 리스트 반환."""
    m, _ = _make_monitor()
    rss_xml = """<?xml version="1.0"?>
    <rss><channel>
        <item><link>https://blog.naver.com/ranto28/2230001111</link></item>
        <item><link>https://blog.naver.com/ranto28/2230002222</link></item>
    </channel></rss>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = rss_xml.encode("utf-8")

    with patch("src.collect.mer_monitor.requests.get", return_value=mock_resp):
        result = m._get_recent_log_nos()

    assert result == ["2230001111", "2230002222"]


def test_get_recent_log_nos_rss_empty_falls_back_to_api():
    """RSS에서 log_no를 못 찾으면 API 폴백."""
    m, _ = _make_monitor()

    rss_resp = MagicMock()
    rss_resp.status_code = 200
    rss_resp.content = b"<?xml version='1.0'?><rss><channel></channel></rss>"

    api_resp = MagicMock()
    api_resp.text = '"logNo":"3330001111","logNo":"3330002222"'

    with patch("src.collect.mer_monitor.requests.get", side_effect=[rss_resp, api_resp]):
        result = m._get_recent_log_nos()

    assert "3330001111" in result
    assert "3330002222" in result


def test_get_recent_log_nos_rss_error_falls_back():
    """RSS 에러 시 API 폴백."""
    m, _ = _make_monitor()

    api_resp = MagicMock()
    api_resp.text = '"logNo":"4440001111"'

    with patch("src.collect.mer_monitor.requests.get",
               side_effect=[Exception("network error"), api_resp]):
        result = m._get_recent_log_nos()

    assert "4440001111" in result


def test_get_recent_log_nos_both_fail():
    """RSS와 API 모두 실패 시 빈 리스트."""
    m, _ = _make_monitor()

    with patch("src.collect.mer_monitor.requests.get",
               side_effect=[Exception("fail1"), Exception("fail2")]):
        result = m._get_recent_log_nos()

    assert result == []


# ─── _scrape_post ─────────────────────────────────────────────────────────────

def test_scrape_post_success():
    """정상적인 포스트 스크래핑."""
    m, _ = _make_monitor()

    html = """
    <html>
    <head><title>테스트 포스트 | 네이버 블로그</title></head>
    <body>
        <span class="blog_date">2024. 1. 15.</span>
        <div class="se-main-container">
            <p>본문 내용입니다.</p>
        </div>
    </body>
    </html>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("src.collect.mer_monitor.requests.get", return_value=mock_resp):
        result = m._scrape_post("1234567890")

    assert result is not None
    assert result["log_no"] == "1234567890"
    assert "본문 내용" in result["content_text"]


def test_scrape_post_network_error():
    """네트워크 에러 시 None 반환."""
    m, _ = _make_monitor()

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("404")

    with patch("src.collect.mer_monitor.requests.get", return_value=mock_resp):
        result = m._scrape_post("9999999999")

    assert result is None


def test_scrape_post_no_content():
    """컨텐츠 영역 없는 페이지."""
    m, _ = _make_monitor()

    html = "<html><head><title>빈 페이지</title></head><body></body></html>"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("src.collect.mer_monitor.requests.get", return_value=mock_resp):
        result = m._scrape_post("1111111111")

    assert result is not None
    assert result["content_text"] == ""


# ─── check_new ────────────────────────────────────────────────────────────────

async def test_check_new_no_log_nos():
    """log_no가 없으면 빈 리스트."""
    m, conn = _make_monitor()
    with patch.object(m, "_get_recent_log_nos", return_value=[]):
        result = await m.check_new(conn)
    assert result == []


async def test_check_new_all_existing():
    """모든 log_no가 이미 DB에 있으면 빈 리스트."""
    m, conn = _make_monitor()
    conn.fetch = AsyncMock(return_value=[{"log_no": "111"}, {"log_no": "222"}])

    with patch.object(m, "_get_recent_log_nos", return_value=["111", "222"]):
        result = await m.check_new(conn)
    assert result == []


async def test_check_new_with_new_posts():
    """신규 포스트가 있으면 스크래핑 후 CollectedPost 반환."""
    m, conn = _make_monitor()
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)

    post_data = {
        "log_no": "555",
        "title": "테스트",
        "date": None,
        "url": "https://blog.naver.com/ranto28/555",
        "content_text": "내용",
    }

    with patch.object(m, "_get_recent_log_nos", return_value=["555"]), \
         patch.object(m, "_scrape_post", return_value=post_data):
        result = await m.check_new(conn)

    assert len(result) == 1
    assert isinstance(result[0], CollectedPost)
    assert result[0].external_id == "555"


async def test_check_new_scrape_returns_none():
    """스크래핑 실패(None) 시 건너뛰기."""
    m, conn = _make_monitor()
    conn.fetch = AsyncMock(return_value=[])

    with patch.object(m, "_get_recent_log_nos", return_value=["666"]), \
         patch.object(m, "_scrape_post", return_value=None):
        result = await m.check_new(conn)

    assert result == []


# ─── _save_post ───────────────────────────────────────────────────────────────

async def test_save_post():
    """_save_post가 올바른 SQL 실행."""
    m, conn = _make_monitor()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)

    post = {
        "log_no": "123",
        "title": "제목",
        "date": None,
        "url": "https://blog.naver.com/ranto28/123",
        "content_text": "본문",
    }

    await m._save_post(conn, post)
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "INSERT INTO mer_posts" in sql


# ─── SourceCollector Protocol ─────────────────────────────────────────────────

def test_mer_monitor_is_source_collector():
    """MerMonitor가 SourceCollector 프로토콜을 구현."""
    from src.collect.source_protocol import SourceCollector
    m, _ = _make_monitor()
    assert isinstance(m, SourceCollector)


def test_mer_monitor_source_name():
    """source_name 기본값."""
    m, _ = _make_monitor()
    assert m.source_name == "mer_ranto28"
