"""eval/eval_dataset.py 단위 테스트."""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.eval.eval_dataset import load_dataset, save_template, TestCase


def test_load_dataset_nonexistent():
    result = load_dataset("/nonexistent/path/gold.json")
    assert result == []


def test_load_dataset_basic(tmp_path):
    data = {
        "test_cases": [
            {
                "id": "eval_001",
                "query": "금리 전망",
                "relevant_insight_ids": [1, 2, 3],
                "expected_topics": ["금리"],
                "expected_stance": "인하 전망",
                "difficulty": "easy",
            }
        ]
    }
    p = tmp_path / "gold.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    cases = load_dataset(p)
    assert len(cases) == 1
    assert cases[0].id == "eval_001"
    assert cases[0].query == "금리 전망"
    assert cases[0].relevant_insight_ids == [1, 2, 3]
    assert cases[0].expected_topics == ["금리"]
    assert cases[0].difficulty == "easy"


def test_load_dataset_defaults(tmp_path):
    data = {
        "test_cases": [
            {
                "id": "eval_002",
                "query": "환율",
            }
        ]
    }
    p = tmp_path / "gold.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    cases = load_dataset(p)
    assert len(cases) == 1
    assert cases[0].relevant_insight_ids == []
    assert cases[0].expected_topics == []
    assert cases[0].expected_stance == ""
    assert cases[0].difficulty == "medium"


def test_load_dataset_empty(tmp_path):
    p = tmp_path / "gold.json"
    p.write_text('{"test_cases": []}', encoding="utf-8")
    cases = load_dataset(p)
    assert cases == []


def test_load_dataset_multiple(tmp_path):
    data = {
        "test_cases": [
            {"id": "e1", "query": "q1", "relevant_insight_ids": [1]},
            {"id": "e2", "query": "q2", "relevant_insight_ids": [2, 3]},
            {"id": "e3", "query": "q3"},
        ]
    }
    p = tmp_path / "gold.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    cases = load_dataset(p)
    assert len(cases) == 3


def test_save_template(tmp_path):
    p = tmp_path / "template.json"
    save_template(p)

    assert p.exists()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    assert "test_cases" in data
    assert len(data["test_cases"]) == 2
    assert data["test_cases"][0]["id"] == "eval_001"


def test_save_template_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "dir" / "template.json"
    save_template(p)
    assert p.exists()


def test_testcase_dataclass():
    tc = TestCase(id="t1", query="q1", relevant_insight_ids=[1, 2])
    assert tc.id == "t1"
    assert tc.query == "q1"
    assert tc.relevant_insight_ids == [1, 2]
    assert tc.expected_topics == []
    assert tc.expected_stance == ""
    assert tc.difficulty == "medium"
