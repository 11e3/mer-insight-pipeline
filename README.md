# mer-insight-pipeline

[![CI](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml/badge.svg)](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml)
[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg?token=WEE9EGD2QP)](https://codecov.io/gh/11e3/mer-insight-pipeline)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

경제 인플루언서의 예측을 자동 추출하고, 실제로 맞았는지 뉴스 DB로 검증하는 파이프라인.

**[라이브 대시보드](https://mer-insight-pipeline.streamlit.app/)** · [English](README_EN.md) · [실험 기록](docs/verification-experiments.md)

<img src="docs/png/dashboard.png" alt="대시보드 스크린샷" width="100%">

---

### 왜 이게 어려운가?

한국어 경제 블로그에는 종목 코드도, 날짜도, 확신도도 없습니다. "유가가 오를 것이다"라는 문장에서 **무엇이 예측이고, 언제까지 검증해야 하고, 뭘 기준으로 맞다/틀리다를 판단하는지** — 이 구조화 자체가 기성 도구로 해결 안 되는 NLP 문제입니다.

자동 검증은 더 어렵습니다. API web_search로 1건씩 검증하면 5,000건에 175달러. 6가지 방식을 실험한 끝에 뉴스 헤드라인 DB + 키워드/벡터 하이브리드 매칭 + Batch API로 비용을 **3달러로 87% 절감**했습니다.

### 지표

| | 값 |
|---|---|
| 추적 중인 예측 | 5,010건 |
| 자동 검증 완료 | 669건 (적중률 73.1%) |
| 뉴스 헤드라인 DB | 54,461건 |
| 검증 비용 | ~$3 (전체) |
| 테스트 | 235개, 90%+ 커버리지 |

---

## 아키텍처

```mermaid
flowchart TD
    subgraph 수집
        BLOG[네이버 블로그] -->|RSS| MON[mer_monitor]
        MON -->|Claude 추출| DB[(PostgreSQL + pgvector)]
        NEWS_RSS[Google News 33피드] --> NC[news_collector]
        NEWS_NAVER[Naver API 14쿼리] --> NC
        NC --> NHL[(news_headlines 54K)]
    end

    subgraph 검증
        DB -->|PENDING 예측| HM[headline_matcher]
        NHL -->|1차 키워드 GIN<br>2차 벡터 cosine| HM
        HM -->|매칭된 헤드라인| HAIKU[Haiku Batch API]
        HAIKU -->|verdict + reason| DB
    end

    DB --> DASH[Streamlit 대시보드]
```

**일일 파이프라인** (GitHub Actions, KST 01:00):

1. 메르 블로그 신규 글 수집 + Claude 인사이트 추출
2. 뉴스 헤드라인 수집 (RSS 33피드 + Naver API)
3. 자동 검증 (헤드라인 매칭 → Haiku 판정)

---

## 검증 방식

뉴스 헤드라인 DB에서 예측과 관련된 기사를 찾고, Haiku가 판정합니다.

```
예측 → 키워드 추출 (kiwipiepy) → GIN 매칭 → 실패 시 벡터 cosine fallback
    → 매칭된 헤드라인 + 예측 → Haiku → CORRECT / INCORRECT / PENDING
```

| 항목 | 수치 |
|------|------|
| 매칭 방식 | 키워드 GIN + 벡터 cosine fallback |
| 뉴스 소스 | Google News RSS 33피드 + Naver API 14쿼리 |
| 헤드라인 | 54,461건 (2022~2026) |
| 판정 모델 | Haiku 4.5 (Batch API, 50% 할인) |
| 검증 비용 | 건당 $0.0045 |

<details>
<summary><strong>6가지 방식 비교 → 87% 비용 절감</strong> (클릭해서 펼치기)</summary>

| 방식 | 일치율 | 비용/건 | 비고 |
|------|--------|---------|------|
| API만 (검색 없음) | 16.9% | $0.01 | 80% PENDING — knowledge cutoff |
| API + Brave 원샷 검색 | 37.7% | $0.02 | snippet 불충분 |
| API + web_search (Sonnet) | 30% | $0.05 | 불안정 |
| API + 에이전틱 tool_use (Opus) | 40% | $0.26 | 토큰 누적 비용 폭발 |
| API + web_search 1건씩 (Haiku) | 80% | $0.035 | 5,000건 = $175 |
| **뉴스 DB + Batch API** ★ | **실전** | **$0.0045** | **5,000건 = ~$3** |

77건 대상 실험 + 50건 블라인드 감사 → 데이터 오염 발견 → 전체 리셋 후 1건씩 재검증.
자세한 내용: [실험 기록](docs/verification-experiments.md)

</details>

### 현재 현황

| 상태 | 건수 |
|------|------|
| CORRECT | 489 |
| INCORRECT | 180 |
| 검증 가능 PENDING | 1,124 |
| 검증 불가 (모호/조건부) | 2,187 |
| 미래 대기 | 968 |

---

## 검색 인프라

하이브리드 BM25 + pgvector 검색으로 25,090개 인사이트에서 관련 문맥을 검색합니다.

| α (BM25 가중치) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| 0.0 (vector) | 0.199 | 0.995 | **0.995** |
| **0.6** (prod) | **0.200** | **1.000** | 0.968 |
| 1.0 (BM25) | 0.196 | 0.980 | 0.935 |

**α=0.6** — Recall 1.000 달성하는 유일한 설정.

---

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| LLM | Claude Sonnet 4.6 (추출) / Haiku 4.5 (검증, Batch API) |
| 임베딩 | `intfloat/multilingual-e5-large` (1024차원, 로컬) |
| DB | PostgreSQL 16 + pgvector (HNSW) |
| 검색 | BM25 (kiwipiepy) + pgvector → RRF 융합 |
| 뉴스 | Google News RSS + Naver API + feedparser |
| 스케줄러 | GitHub Actions cron |
| 대시보드 | Streamlit Cloud |

---

## 빠른 시작

```bash
git clone https://github.com/11e3/mer-insight-pipeline.git
cd mer-insight-pipeline
cp .env.example .env  # API 키 설정

docker compose up -d db
python scripts/run_batch.py all        # 인사이트 추출
python -m scripts.run_job              # 파이프라인 1회 실행
streamlit run src/dashboard/app.py     # 대시보드
```

---

## 프로젝트 구조

```
src/
├── collect/     # 데이터 수집 (블로그, 뉴스 RSS, Naver API)
├── extract/     # Claude 인사이트 추출 (Batch + 실시간)
├── verify/      # 자동 검증 (headline_matcher + auto_verifier)
├── search/      # 하이브리드 검색 (BM25 + pgvector)
├── embed/       # 임베딩 (multilingual-e5-large)
├── eval/        # 검색 품질 평가 (ablation, LLM judge)
├── pipeline/    # 일일 파이프라인 오케스트레이터
├── dashboard/   # Streamlit 대시보드
└── config/      # 설정, 프롬프트

scripts/ops/     # 배치 검증, 뉴스 백필, 데이터 운영
```

---

## 비용

| 항목 | 비용 |
|------|------|
| 인사이트 추출 | ~$0.01/글 |
| 자동 검증 | ~$0.0045/건 |
| 뉴스 수집 | $0 |
| 임베딩 | $0 (로컬) |
| **월 운영** | **~$0.50** |

---

## 라이선스

MIT
