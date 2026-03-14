"""
Eval Dataset 관리: 골드 데이터셋 로드/저장/검증.

골드 데이터셋 형식 (eval_data/gold.json):
{
  "test_cases": [
    {
      "id": "eval_001",
      "query": "미국 국채 금리 전망에 대한 메르의 분석",
      "relevant_insight_ids": [1234, 5678],
      "expected_topics": ["미국 국채", "금리", "연준"],
      "expected_stance": "금리 인하 지연 전망",
      "difficulty": "medium"
    }
  ]
}
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent.parent.parent / "eval_data" / "gold.json"


@dataclass
class TestCase:
    id: str
    query: str
    relevant_insight_ids: list[int]
    expected_topics: list[str] = field(default_factory=list)
    expected_stance: str = ""
    difficulty: str = "medium"  # easy | medium | hard


def load_dataset(path: str | Path = _DEFAULT_PATH) -> list[TestCase]:
    """JSON 파일에서 TestCase 리스트 로드."""
    p = Path(path)
    if not p.exists():
        log.warning(f"데이터셋 파일 없음: {p}. 빈 리스트 반환.")
        return []

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    cases = []
    for raw in data.get("test_cases", []):
        cases.append(TestCase(
            id=raw["id"],
            query=raw["query"],
            relevant_insight_ids=[int(i) for i in raw.get("relevant_insight_ids", [])],
            expected_topics=raw.get("expected_topics", []),
            expected_stance=raw.get("expected_stance", ""),
            difficulty=raw.get("difficulty", "medium"),
        ))
    log.info(f"데이터셋 로드: {len(cases)}개 (from {p})")
    return cases


def save_template(path: str | Path = _DEFAULT_PATH) -> None:
    """템플릿 골드 데이터셋 저장 (처음 시작할 때 사용)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    template = {
        "test_cases": [
            {
                "id": "eval_001",
                "query": "미국 국채 금리 전망에 대한 메르의 분석",
                "relevant_insight_ids": [],
                "expected_topics": ["미국 국채", "금리", "연준"],
                "expected_stance": "금리 인하 지연 전망",
                "difficulty": "medium",
            },
            {
                "id": "eval_002",
                "query": "한국 부동산 시장 전망",
                "relevant_insight_ids": [],
                "expected_topics": ["부동산", "아파트", "금리"],
                "expected_stance": "",
                "difficulty": "easy",
            },
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
    log.info(f"템플릿 저장: {path}")
