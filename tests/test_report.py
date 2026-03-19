"""eval/report.py 단위 테스트."""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
    os.environ.setdefault(_var, "test")

from src.eval.report import generate_report, _render_markdown
from src.eval.metrics import CaseResult, RetrievalMetrics, GenerationMetrics


def _make_cases():
    return [
        CaseResult(
            case_id="c1", query="금리 전망",
            retrieval=RetrievalMetrics(precision_at_k=0.8, recall_at_k=1.0, mrr=1.0, k=5),
            generation=GenerationMetrics(context_relevance=0.9, faithfulness=0.8,
                                         answer_relevance=0.7, hallucination_rate=0.05),
        ),
        CaseResult(
            case_id="c2", query="환율 예측",
            retrieval=RetrievalMetrics(precision_at_k=0.4, recall_at_k=0.5, mrr=0.5, k=5),
            generation=GenerationMetrics(context_relevance=0.6, faithfulness=0.5,
                                         answer_relevance=0.4, hallucination_rate=0.1),
        ),
    ]


def test_generate_report_creates_files(tmp_path):
    cases = _make_cases()
    md_path = generate_report(cases, tmp_path)

    assert md_path.exists()
    assert md_path.suffix == ".md"

    # JSON file should also exist
    json_files = list(tmp_path.glob("eval_*.json"))
    assert len(json_files) == 1

    with open(json_files[0], encoding="utf-8") as f:
        data = json.load(f)
    assert data["summary"]["n"] == 2
    assert len(data["cases"]) == 2


def test_generate_report_creates_output_dir(tmp_path):
    out_dir = tmp_path / "nested" / "dir"
    cases = _make_cases()
    md_path = generate_report(cases, out_dir)
    assert md_path.exists()
    assert out_dir.exists()


def test_render_markdown_content():
    cases = _make_cases()
    from src.eval.metrics import aggregate
    agg = aggregate(cases)
    md = _render_markdown(agg, cases, "20240115_120000")

    assert "# Eval Report" in md
    assert "Precision@5" in md
    assert "Recall@5" in md
    assert "MRR" in md
    assert "Context Relevance" in md
    assert "Worst Cases" in md
    # c2 has lower precision, should be in worst cases
    assert "c2" in md


def test_render_markdown_empty():
    md = _render_markdown({}, [], "20240101_000000")
    assert "# Eval Report" in md
    assert "0개" in md


def test_generate_report_empty_cases(tmp_path):
    md_path = generate_report([], tmp_path)
    assert md_path.exists()
