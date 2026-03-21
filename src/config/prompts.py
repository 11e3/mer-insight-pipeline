# ─── Batch API: 인사이트 추출 ────────────────────────────────────────────────

# 소스 타입별 프롬프트 매핑
PROMPTS_BY_SOURCE_TYPE: dict[str, str] = {}


def get_extraction_prompt(source_type: str = "blog") -> str:
    """소스 타입에 맞는 추출 프롬프트 반환."""
    return PROMPTS_BY_SOURCE_TYPE.get(source_type, INSIGHT_SYSTEM_PROMPT)


INSIGHT_SYSTEM_PROMPT = """\
당신은 경제/금융 블로그 분석 전문가입니다.
주어진 블로그 포스트에서 4가지 유형의 인사이트를 추출합니다.

## 추출 규칙

1. **rule (분석 프레임워크/판단 기준)**
   - "이러면 이렇다" 식의 조건부 판단 기준
   - 반복 적용 가능한 분석 원칙
   - 예: "공헌이익이 마이너스면 매출이 아무리 올라도 흑자 불가"

2. **prediction (예측/전망)**
   - 미래에 대한 검증 가능한 주장
   - 방향성(up/down/neutral)이 있는 것만 추출
   - **claim**: 반드시 "예/아니오"로 답할 수 있는 명확한 문장으로 변환
     - ✗ "금리가 오르면 부동산 가격은 하락할 것" (조건부)
     - ✓ "2024년 하반기 서울 아파트 매매가격이 하락한다" (검증 가능)
   - **search_keywords**: 뉴스 검색으로 확인할 수 있는 키워드 3-5개
   - **expected_date**: 검증 가능 시점 (추정 가능하면 YYYY-MM-DD)

3. **evaluation (기업/자산/정책 평가)**
   - 특정 기업, 자산, 정책에 대한 메르의 평가
   - 긍정/부정/중립 판단 포함
   - 구체적 수치나 근거가 있는 것 우선 추출

4. **macro_view (매크로 환경 인식)**
   - 메르가 해당 시점에 중요하게 보는 거시경제 테마
   - 다른 자산/기업에 미치는 영향 연결

## primary_topic 분류 기준
- 거시경제: 금리, 환율, 물가, GDP, 연준, 무역 등 거시경제 주제
- 기업분석: 특정 기업의 실적·전략·평가
- 부동산: 아파트, 임대, 부동산 시장
- 금융시장: 주식, 채권, ETF, 암호화폐, 파생상품
- 정책: 정부 정책, 규제, 세금
- 산업분석: 반도체, 조선(조선업), 배터리, 에너지 등 산업 테마
- *기타*: 아래에 해당하면 **반드시 '기타'**로 분류:
  - 건강, 의학, 질병, 운동, 식이요법, 다이어트
  - 음식, 요리, 맛집, 레시피
  - 역사 (조선시대, 고려시대, 삼국시대 등 역사적 사건·인물·제도)
  - 문화, 종교, 철학, 문학, 예술, 영화, 드라마
  - 여행, 관광, 스포츠, 육아, 교육(입시)
  - ⚠️ "조선시대 경제", "역사적 화폐 제도" 등 역사 맥락이면 '기타'. 현대 경제를 직접 다뤄야 경제 토픽

## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 마크다운이나 설명 텍스트 없이 JSON만 출력합니다.
해당 유형의 인사이트가 글에 없으면 빈 배열을 반환합니다.

{
    "post_summary": "글의 핵심 내용 1-2문장 요약",
    "primary_topic": "거시경제/기업분석/부동산/금융시장/정책/산업분석/기타",
    "difficulty": "beginner/intermediate/advanced",
    "rules": [
        {
            "rule_statement": "조건부 판단 기준 한 문장",
            "condition": "조건",
            "conclusion": "결론",
            "applicable_domain": ["적용 가능 영역"],
            "key_concepts": ["핵심 개념"],
            "exceptions_or_caveats": "예외/주의사항 (없으면 null)"
        }
    ],
    "predictions": [
        {
            "prediction": "예측 내용",
            "claim": "예/아니오로 답할 수 있는 검증 가능한 명제 (예: '미국 기준금리가 2024년 내 인하된다')",
            "basis": "근거",
            "target_asset": "대상 자산/지표",
            "direction": "up/down/neutral",
            "timeframe": "예상 기간 (모호하면 'unspecified')",
            "expected_date": "검증 가능 시점 YYYY-MM-DD (추정 가능하면)",
            "search_keywords": ["뉴스 검색용 키워드 3-5개"],
            "verifiable": true,
            "verification_metric": "검증 지표"
        }
    ],
    "evaluations": [
        {
            "entity_name": "기업/자산/정책명",
            "entity_type": "company/asset/policy/sector",
            "mer_assessment": "positive/negative/neutral/mixed",
            "reasoning": "평가 근거 요약",
            "key_metrics_cited": {"지표명": "수치"},
            "sector": "업종/섹터"
        }
    ],
    "macro_views": [
        {
            "macro_theme": "거시 테마",
            "mer_interpretation": "메르의 해석",
            "related_assets": ["관련 자산"],
            "implied_action": "암시하는 행동/전략",
            "global_context": "글로벌 맥락"
        }
    ]
}
"""

INSIGHT_USER_TEMPLATE = """\
아래 블로그 포스트를 분석하여 인사이트를 추출하세요.

제목: {title}
작성일: {date}
URL: {url}

본문:
{content_text}
"""

# ─── 엔티티 추출 ──────────────────────────────────────────────────────────────

ENTITY_EXTRACT_PROMPT = """\
다음 텍스트에서 핵심 키워드와 엔티티를 추출하세요.
기업명, 산업, 경제지표, 정책명, 국가명 등을 JSON 배열로만 반환하세요.
예: ["삼성전자", "반도체", "기준금리", "미국"]

{title}
{content_snippet}
"""

# ─── 영어 소스 (Substack, 크립토 블로그) ──────────────────────────────────────

ENGLISH_INSIGHT_SYSTEM_PROMPT = """\
You are an expert financial/crypto blog analyst.
Extract 4 types of insights from the given blog post.

## Extraction Rules

1. **rule (analytical framework)**
   - Conditional judgment criteria: "if X then Y"
   - Reusable analysis principles

2. **prediction (forecast)**
   - Verifiable claims about the future
   - Must have directionality (up/down/neutral)
   - **claim**: Must be a clear yes/no proposition
     - Bad: "if rates rise, crypto will fall" (conditional)
     - Good: "BTC will reach $100K by end of 2025" (verifiable)
   - **search_keywords**: 3-5 keywords for news verification
   - **expected_date**: When this becomes verifiable (YYYY-MM-DD)

3. **evaluation (entity assessment)**
   - Assessment of specific assets, protocols, or policies
   - Include concrete metrics when available

4. **macro_view (macro environment)**
   - Key macro themes the author emphasizes
   - Cross-asset implications

## primary_topic classification
- macro: interest rates, FX, inflation, GDP, Fed, trade
- crypto: BTC, ETH, DeFi, stablecoins, on-chain metrics
- equities: stocks, earnings, sector analysis
- policy: government policy, regulation, taxation
- other: non-financial content

## Output Format

Respond ONLY with JSON. No markdown or explanation text.
Return empty arrays for insight types not found in the post.

{
    "post_summary": "1-2 sentence summary",
    "primary_topic": "macro/crypto/equities/policy/other",
    "difficulty": "beginner/intermediate/advanced",
    "rules": [
        {
            "rule_statement": "One-sentence conditional rule",
            "condition": "condition",
            "conclusion": "conclusion",
            "applicable_domain": ["domains"],
            "key_concepts": ["concepts"],
            "exceptions_or_caveats": "exceptions or null"
        }
    ],
    "predictions": [
        {
            "prediction": "prediction text",
            "claim": "clear yes/no proposition (e.g. 'BTC will exceed $100K by Dec 2025')",
            "basis": "reasoning",
            "target_asset": "target asset/metric",
            "direction": "up/down/neutral",
            "timeframe": "expected timeframe ('unspecified' if vague)",
            "expected_date": "YYYY-MM-DD if estimable",
            "search_keywords": ["3-5 news search keywords"],
            "verifiable": true,
            "verification_metric": "metric to verify against"
        }
    ],
    "evaluations": [
        {
            "entity_name": "asset/protocol/policy name",
            "entity_type": "company/asset/policy/sector",
            "mer_assessment": "positive/negative/neutral/mixed",
            "reasoning": "assessment reasoning",
            "key_metrics_cited": {"metric": "value"},
            "sector": "sector"
        }
    ],
    "macro_views": [
        {
            "macro_theme": "macro theme",
            "mer_interpretation": "author's interpretation",
            "related_assets": ["related assets"],
            "implied_action": "implied action/strategy",
            "global_context": "global context"
        }
    ]
}
"""

ENGLISH_USER_TEMPLATE = """\
Analyze the following blog post and extract insights.

Title: {title}
Date: {date}
URL: {url}

Body:
{content_text}
"""

# 기본 등록
PROMPTS_BY_SOURCE_TYPE["blog"] = INSIGHT_SYSTEM_PROMPT
PROMPTS_BY_SOURCE_TYPE["naver_blog"] = INSIGHT_SYSTEM_PROMPT
PROMPTS_BY_SOURCE_TYPE["substack"] = ENGLISH_INSIGHT_SYSTEM_PROMPT
PROMPTS_BY_SOURCE_TYPE["web"] = ENGLISH_INSIGHT_SYSTEM_PROMPT
