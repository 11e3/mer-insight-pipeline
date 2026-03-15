# config/prompts.example.py
#
# Copy this file to config/prompts.py and fill in your own prompts.
# prompts.py is gitignored — this example shows the required structure only.

# ─── Batch API: 인사이트 추출 ────────────────────────────────────────────────

INSIGHT_SYSTEM_PROMPT = """\
[Your system prompt for extracting insights from blog posts]

Required output JSON schema:
{
    "post_summary": "str",
    "primary_topic": "거시경제|기업분석|부동산|금융시장|정책|산업분석|기타",
    "difficulty": "beginner|intermediate|advanced",
    "rules": [
        {
            "rule_statement": "str",
            "condition": "str",
            "conclusion": "str",
            "applicable_domain": ["str"],
            "key_concepts": ["str"],
            "exceptions_or_caveats": "str|null"
        }
    ],
    "predictions": [
        {
            "prediction": "str",
            "basis": "str",
            "target_asset": "str",
            "direction": "up|down|neutral",
            "timeframe": "str",
            "verifiable": true,
            "verification_metric": "str"
        }
    ],
    "evaluations": [
        {
            "entity_name": "str",
            "entity_type": "company|asset|policy|sector",
            "mer_assessment": "positive|negative|neutral|mixed",
            "reasoning": "str",
            "key_metrics_cited": {"str": "str"},
            "sector": "str"
        }
    ],
    "macro_views": [
        {
            "macro_theme": "str",
            "mer_interpretation": "str",
            "related_assets": ["str"],
            "implied_action": "str",
            "global_context": "str"
        }
    ]
}
"""

INSIGHT_USER_TEMPLATE = """\
[Template for per-post analysis requests]

Variables: {title}, {date}, {url}, {content_text}
"""

# ─── 엔티티 추출 ──────────────────────────────────────────────────────────────

ENTITY_EXTRACT_PROMPT = """\
[Prompt for extracting key entities/keywords from text]

Variables: {title}, {content_snippet}
"""
