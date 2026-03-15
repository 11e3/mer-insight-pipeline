# mer-insight-pipeline

**mer-insight-pipeline** automates financial prediction tracking and verification from [Mer (ranto28)](https://blog.naver.com/ranto28)'s 2,193 Korean finance blog posts using macro data from FRED, BOK ECOS, DART, Naver Finance, Fed/BOK RSS, and Google News.

The pipeline extracts predictions with Claude Batch API, collects real market data from 6 external sources, and verifies each prediction daily with Claude Haiku as an automated judge — 5,010 predictions tracked so far. Retrieval is powered by hybrid BM25 + pgvector search (25,090 indexed insights, RRF fusion at α=0.6) on PostgreSQL with no vector-DB vendor lock-in.

[![CI](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml/badge.svg)](https://github.com/11e3/mer-insight-pipeline/actions)
[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg)](https://codecov.io/gh/11e3/mer-insight-pipeline)
[![Dashboard](https://img.shields.io/badge/dashboard-Streamlit-FF4B4B?logo=streamlit)](http://34.50.19.176:8501)

[한국어 README](README_KR.md)

---

## Architecture

```mermaid
flowchart TD
    subgraph Batch Extraction
        A[Naver Blog<br>2,193 posts] -->|scrape| B[parse_results.py]
        B -->|rules / predictions<br>evaluations / macro_views| C[(PostgreSQL<br>+ pgvector)]
        C --> EMB[local_embedder.py<br>1024-dim vectors]
        EMB --> C
    end

    subgraph "Daily Pipeline (20:00)"
        ED[event_dispatcher.py] -->|1| MER[mer_monitor<br>신규 글 수집]
        ED -->|2| DC[dart_collector<br>DART 공시]
        ED -->|2| LM[load_macro<br>FRED · BOK ECOS]
        ED -->|2| NC[news_collector<br>Fed · BOK · Google News]
        ED -->|3| PV[prediction_verifier<br>Claude Haiku judge]
        MER --> C
        DC & LM & NC --> C
        PV -->|CORRECT / INCORRECT / PENDING| C
    end

    subgraph Hybrid Search
        HS1[BM25<br>kiwipiepy] & HS2[pgvector<br>HNSW] -->|RRF α=0.6| HS3[HybridSearcher]
    end

    PV -.->|context lookup| HS3
    HS3 --> C

    C --> Q[Streamlit Dashboard<br>port 8501]

    EV[eval_runner.py<br>offline ablation] -.-> HS3
```

---

## Prediction Verification

Every `prediction`-type insight extracted from Mer's posts is stored in `mer_predictions` and verified daily by Claude Haiku acting as an automated judge.

All data sources listed below are collected daily and fed to Claude as verification context.

**Verdicts**

| Verdict | Condition |
|---------|-----------|
| `CORRECT` | Predicted outcome confirmed by context or Claude's knowledge |
| `INCORRECT` | Predicted outcome contradicted by evidence |
| `PENDING` | Condition not yet met, or insufficient information — re-checked next day |

Predictions stay in the queue until resolved — no expiry. `BATCH_SIZE=40` predictions per Haiku call with **prompt caching** enabled (~90% input cost reduction on cached context).

---

## Macro Data Pipeline

| Source | Data |
|--------|------|
| FRED API | VIX, US 10Y Treasury, WTI crude, BTC/USD, Fed Funds Rate, CPI YoY, Unemployment |
| BOK ECOS | USD/KRW, KOSPI, KOSDAQ, Korea base rate |
| Naver Finance | Monthly H/L/C + latest close for 10 major Korean stocks |
| DART | Corporate disclosure filings via RSS |
| Fed / BOK RSS | Central bank press releases, rate decisions |
| Google News | Geopolitical events — sanctions, tariffs, trade war keywords |

All data is collected once daily before prediction verification and fed to Claude Haiku as context for judging predictions.

---

## Search Infrastructure

Hybrid BM25 + vector search serves as the retrieval backbone for context assembly and the eval pipeline.

Query embeddings use `intfloat/multilingual-e5-large` (1024-dim) — the same model used to index the production DB.

**Alpha ablation** — N=200 queries, K=5:

| α (BM25 weight) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| **α=0.0** | 0.199 | 0.995 | **0.995** |
| **α=0.6** ★ | **0.200** | **1.000** | 0.968 |
| α=1.0 | 0.196 | 0.980 | 0.935 |

Production default: **α=0.6** — the only setting that achieves perfect Recall (1.000).

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| LLM (analysis · verification) | `claude-sonnet-4-6` |
| LLM (extraction, default) | `claude-sonnet-4-6` (Haiku optional via `--haiku`) |
| Batch API | Anthropic Batch API |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim, local) |
| Vector DB | PostgreSQL 16 + pgvector (HNSW index) |
| Keyword Search | rank-bm25 + kiwipiepy (Korean morphological analysis) |
| Hybrid Fusion | Reciprocal Rank Fusion (RRF, α=0.6) |
| Scheduler | APScheduler (local) / GCP Cloud Scheduler + Cloud Run Job |
| Data Sources | FRED, BOK ECOS, DART, Naver Finance, Fed/BOK RSS, Google News |
| Dashboard | Streamlit |

---

## Metrics

| Metric | Value |
|--------|-------|
| Processed posts | 2,193 |
| Extracted insights | 25,090 |
| Tracked predictions | 5,010 |
| Insight types | 4 (rule, prediction, evaluation, macro_view) |
| Embedding dimensions | 1024 |
| BM25 index size | 19,702 documents |

---

## Setup

### Prerequisites

- Docker & Docker Compose
- Anthropic API key

### Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/11e3/mer-insight-pipeline.git
cd mer-insight-pipeline
cp .env.example .env        # fill in your API keys
cp config/prompts.example.py config/prompts.py

# 2. Start the database
docker compose up -d db

# 3. Run batch extraction (2,193 posts → 25,090 insights)
python scripts/run_batch.py all

# 4. Build BM25 index cache
python -m src.search.bm25_index

# 5. Run pipeline once (or start the daily scheduler)
python -m scripts.run_job                 # run once
python -m src.pipeline.event_dispatcher   # daily 20:00 scheduler
```

### Daily Pipeline

Runs once daily at 20:00 (KST) via Cloud Scheduler or APScheduler:

1. Mer blog — check for new posts, extract predictions
2. DART / FRED / BOK ECOS / News RSS — collect latest data
3. Prediction verification — Claude Haiku judges all pending predictions

### Dashboard

```bash
streamlit run src/dashboard/prediction_dashboard.py   # http://localhost:8501
```

### Eval

```bash
python -m src.eval.eval_runner --mode retrieval_only
python -m src.search.experiment --mode ablation --k 5
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✓ | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | ✓ | Claude API key |
| `GCP_PROJECT_ID` | optional | GCP project ID (for Vertex AI embeddings) |
| `GCP_LOCATION` | optional | Vertex AI region (default: us-central1) |
| `FRED_API_KEY` | optional | FRED economic data (free) |
| `BOK_API_KEY` | optional | Bank of Korea ECOS API (free) |

---

## Project Structure

```
mer-insight-pipeline/
├── config/
│   ├── settings.py               # All configuration, loaded from .env
│   └── prompts.example.py        # Prompt structure template
├── src/
│   ├── search/                   # Search Infrastructure
│   │   ├── bm25_index.py         # BM25 with kiwipiepy tokenizer + pickle cache
│   │   ├── vector_index.py       # pgvector HNSW wrapper (1024-dim)
│   │   ├── hybrid.py             # RRF fusion (α=0.6)
│   │   └── experiment.py         # Ablation: vector vs BM25 vs hybrid
│   ├── eval/                     # Eval Pipeline
│   │   ├── eval_runner.py        # Main runner (--mode retrieval_only | full)
│   │   ├── metrics.py            # Precision@K, Recall@K, MRR
│   │   ├── llm_judge.py          # Context Relevance / Faithfulness / Answer Relevance
│   │   └── report.py             # Markdown + JSON report generator
│   ├── extract/
│   │   ├── batch_api.py          # Claude Batch API orchestration
│   │   ├── embedder.py           # Embedder protocol + factory (LocalEmbedder default)
│   │   ├── local_embedder.py     # multilingual-e5-large 1024-dim
│   │   ├── parse_results.py      # Batch result parsing → DB
│   │   └── realtime_extractor.py # Real-time insight extraction (Haiku)
│   ├── ingest/
│   │   ├── load_macro.py         # FRED / BOK ECOS macro data collection
│   │   └── date_parser.py        # Korean date string parser
│   ├── pipeline/
│   │   ├── event_dispatcher.py   # Main runtime (APScheduler, 6 schedules)
│   │   ├── mer_monitor.py        # Blog RSS watcher
│   │   ├── dart_collector.py     # DART filings (Korean corporate disclosure)
│   │   ├── news_collector.py     # RSS feeds: Fed · BOK · geopolitics
│   │   └── prediction_verifier.py # Daily Claude Haiku batch verification
│   └── dashboard/
│       └── prediction_dashboard.py # Prediction accuracy dashboard
├── app.py                        # Streamlit Cloud entry point
├── eval_data/
│   └── gold_extended.json        # Gold dataset: 200 queries with relevant insight IDs
├── scripts/
│   ├── init_db.sql               # PostgreSQL schema
│   ├── run_batch.py              # Batch extraction orchestrator
│   ├── run_job.py                # Pipeline entry point (single daily run)
│   └── migrate_predictions.py    # One-time: mer_insights → mer_predictions backfill
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## License

MIT
