"""extract/parse_results.py 단위 테스트."""

import os
import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.extract.parse_results import extract_content, INSIGHT_TYPE_MAP


# ─── INSIGHT_TYPE_MAP ────────────────────────────────────────────────────────

def test_insight_type_map_keys():
    assert "rules" in INSIGHT_TYPE_MAP
    assert "predictions" in INSIGHT_TYPE_MAP
    assert "evaluations" in INSIGHT_TYPE_MAP
    assert "macro_views" in INSIGHT_TYPE_MAP


def test_insight_type_map_values():
    assert INSIGHT_TYPE_MAP["rules"] == "rule"
    assert INSIGHT_TYPE_MAP["predictions"] == "prediction"
    assert INSIGHT_TYPE_MAP["evaluations"] == "evaluation"
    assert INSIGHT_TYPE_MAP["macro_views"] == "macro_view"


# ─── extract_content: rule ───────────────────────────────────────────────────

def test_extract_content_rule_with_statement():
    item = {"rule_statement": "금리 오르면 부동산 하락"}
    assert extract_content(item, "rule") == "금리 오르면 부동산 하락"


def test_extract_content_rule_without_statement():
    item = {"condition": "X", "conclusion": "Y"}
    result = extract_content(item, "rule")
    assert "condition" in result  # fallback to json.dumps


# ─── extract_content: prediction ─────────────────────────────────────────────

def test_extract_content_prediction_with_key():
    item = {"prediction": "코스피 3000 돌파"}
    assert extract_content(item, "prediction") == "코스피 3000 돌파"


def test_extract_content_prediction_without_key():
    item = {"basis": "경기 회복"}
    result = extract_content(item, "prediction")
    assert "basis" in result  # fallback


# ─── extract_content: evaluation ─────────────────────────────────────────────

def test_extract_content_evaluation():
    item = {
        "entity_name": "삼성전자",
        "mer_assessment": "positive",
        "reasoning": "실적 개선",
    }
    result = extract_content(item, "evaluation")
    assert "삼성전자" in result
    assert "positive" in result
    assert "실적 개선" in result


def test_extract_content_evaluation_truncated():
    item = {
        "entity_name": "A",
        "mer_assessment": "negative",
        "reasoning": "x" * 400,
    }
    result = extract_content(item, "evaluation")
    assert len(result) <= 300


# ─── extract_content: macro_view ─────────────────────────────────────────────

def test_extract_content_macro_view_with_key():
    item = {"mer_interpretation": "금리 인하 사이클 시작"}
    assert extract_content(item, "macro_view") == "금리 인하 사이클 시작"


def test_extract_content_macro_view_without_key():
    item = {"macro_theme": "인플레이션"}
    result = extract_content(item, "macro_view")
    assert "인플레이션" in result


# ─── extract_content: unknown type ───────────────────────────────────────────

def test_extract_content_unknown_type():
    item = {"key": "value"}
    result = extract_content(item, "unknown")
    assert len(result) <= 200


# ─── parse_and_insert ────────────────────────────────────────────────────────

async def test_parse_and_insert_basic(tmp_path):
    """parse_and_insert가 JSONL을 파싱해서 DB에 삽입하는지."""
    # JSONL 파일 생성
    data = {
        "rules": [{"rule_statement": "test rule"}],
        "predictions": [],
        "evaluations": [],
        "macro_views": [],
    }
    result_line = json.dumps({
        "custom_id": "mer-12345",
        "result": {
            "type": "succeeded",
            "message": {
                "content": [{"text": json.dumps(data)}],
            },
        },
    })
    jsonl_file = tmp_path / "results.jsonl"
    jsonl_file.write_text(result_line + "\n", encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [{"log_no": "12345", "id": 1}]

    with patch("src.extract.parse_results.asyncpg") as mock_asyncpg:
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        from src.extract.parse_results import parse_and_insert
        await parse_and_insert(str(jsonl_file))

    # Should have called execute for the rule insight
    assert mock_conn.execute.call_count >= 1


async def test_parse_and_insert_error_result(tmp_path):
    """error 타입 결과를 건너뛰는지."""
    result_line = json.dumps({
        "custom_id": "mer-12345",
        "result": {"type": "error"},
    })
    jsonl_file = tmp_path / "results.jsonl"
    jsonl_file.write_text(result_line + "\n", encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [{"log_no": "12345", "id": 1}]

    with patch("src.extract.parse_results.asyncpg") as mock_asyncpg:
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        from src.extract.parse_results import parse_and_insert
        await parse_and_insert(str(jsonl_file))

    # No execute calls for error results
    mock_conn.execute.assert_not_called()


async def test_parse_and_insert_json_in_code_block(tmp_path):
    """```json ... ``` 형태 응답 처리."""
    data = {
        "rules": [{"rule_statement": "test"}],
        "predictions": [],
        "evaluations": [],
        "macro_views": [],
    }
    raw_text = "```json\n" + json.dumps(data) + "\n```"
    result_line = json.dumps({
        "custom_id": "mer-99999",
        "result": {
            "type": "succeeded",
            "message": {"content": [{"text": raw_text}]},
        },
    })
    jsonl_file = tmp_path / "results.jsonl"
    jsonl_file.write_text(result_line + "\n", encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [{"log_no": "99999", "id": 2}]

    with patch("src.extract.parse_results.asyncpg") as mock_asyncpg:
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        from src.extract.parse_results import parse_and_insert
        await parse_and_insert(str(jsonl_file))

    assert mock_conn.execute.call_count >= 1


async def test_parse_and_insert_missing_post_id(tmp_path):
    """log_no가 DB에 없으면 건너뛰기."""
    result_line = json.dumps({
        "custom_id": "mer-00000",
        "result": {
            "type": "succeeded",
            "message": {"content": [{"text": "{}"}]},
        },
    })
    jsonl_file = tmp_path / "results.jsonl"
    jsonl_file.write_text(result_line + "\n", encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []  # no posts in DB

    with patch("src.extract.parse_results.asyncpg") as mock_asyncpg:
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        from src.extract.parse_results import parse_and_insert
        await parse_and_insert(str(jsonl_file))

    mock_conn.execute.assert_not_called()
