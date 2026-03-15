# mer-insight-pipeline

**mer-insight-pipeline** automates financial prediction tracking and verification from Mer (ranto28)'s 2,193 blog posts using Korean macro data (FRED, BOK ECOS, DART, Naver Finance).

[Mer (ranto28)](https://blog.naver.com/ranto28), a Korean finance blogger, publishes macro predictions across 2,193 posts. This pipeline extracts those predictions with Claude Batch API, collects real market data from 6 external sources, and verifies each prediction daily with Claude Haiku as an automated judge — 5,368 predictions tracked so far. Retrieval is powered by hybrid BM25 + pgvector search (25,090 indexed insights, α=0.6 optimal) on PostgreSQL with no vector-DB vendor lock-in.

[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg)](https://codecov.io/gh/11e3/mer-insight-pipeline)
[![Demo](https://img.shields.io/badge/demo-Streamlit-FF4B4B?logo=streamlit)](https://mer-insight-pipeline-yvkztwypti7zbnfae8wfjs.streamlit.app)

[한국어 README](README_KR.md)

---

## Architecture

```mermaid
flowchart TD
    A[Naver Blog<br>Korean finance blog] -->|RSS / scrape| B[mer_monitor]
    B --> C[(PostgreSQL<br>+ pgvector)]

    subgraph Batch Pipeline
        C -->|2,193 posts| D[Claude Batch API<br>batch_api.py]
        D -->|JSONL results| E[parse_results.py]
        E -->|rules / predictions<br>evaluations / macro_views| C
        C --> F[local_embedder.py<br>multilingual-e5-large 1024-dim]
        F -->|1024-dim vectors| C
        C --> G[cluster_insights.py<br>DBSCAN dedup]
    end

    subgraph Hybrid Search
        HS1[BM25 Index<br>kiwipiepy + rank_bm25]
        HS2[Vector Index<br>pgvector HNSW]
        HS1 & HS2 -->|RRF fusion α=0.4| HS3[HybridSearcher]
    end

    HS3 --> C

    subgraph Real-time Pipeline
        ED[event_dispatcher.py<br>APScheduler] --> B
        ED --> DC[dart_collector]
        ED --> NC[news_collector<br>Fed · BOK RSS]
        ED --> LM[load_macro<br>FRED · BOK ECOS]
        ED --> PV[prediction_verifier<br>Claude Haiku judge]
        DC & NC & LM --> CA[context_assembler]
        CA --> HS3
        CA --> AG2[analysis_generator<br>Claude Sonnet]
        PV -->|CORRECT/INCORRECT/PENDING| C
    end

    AG2 --> TG[Telegram<br>Tier 1 · Tier 2]

    subgraph Eval Pipeline
        EV[eval_runner.py] --> HS3
        EV --> LJ[LLM Judge<br>Claude Sonnet]
        EV --> RPT[Markdown Report]
    end

    C --> Q[Streamlit Dashboard<br>port 8501]
```

---

## Prediction Verification Pipeline

Every `prediction`-type insight extracted from Mer's posts is stored in `mer_predictions` and verified daily by Claude Haiku acting as an automated judge.

**Context fed to Claude per batch:**

| Source | What's included |
|--------|----------------|
| Monthly macro summary | KOSPI hi/lo/avg, USD/KRW, WTI, US10Y, Fed rate, VIX, BTC — from FRED + BOK ECOS |
| Naver Finance stocks | Monthly H/L/close for historical data + daily close for recent 90 days (10 major equities) |
| DART filings + news | Corporate events collected since the oldest pending prediction date |
| Auto-analyses | Mer post analyses from the same period |

**Verdicts**

| Verdict | Condition |
|---------|-----------|
| `CORRECT` | Predicted outcome confirmed by context or Claude's knowledge |
| `INCORRECT` | Predicted outcome contradicted by evidence |
| `PENDING` | Condition not yet met, or insufficient information — re-checked next day |

Predictions stay in the queue until resolved — no expiry. BATCH_SIZE=20 predictions per Haiku call; the daily 20:00 run processes all open predictions before the daily report is generated.

```bash
# One-time backlog clearance
gcloud run jobs execute report-generator --args="--job,verify_predictions" --region=asia-northeast3
```

**Live accuracy** (auto-updated on every push via CI):

<!-- AUTO:prediction_accuracy -->
<!-- END:prediction_accuracy -->

---

## Macro Data Pipeline

The pipeline collects financial and economic data from 6 external sources on scheduled intervals, stored in `macro_daily` and `events` tables for prediction verification and report generation.

| Source | Data | Schedule | Module |
|--------|------|----------|--------|
| FRED API | VIX, US 10Y Treasury, WTI crude, BTC/USD, Fed Funds Rate, CPI YoY, Unemployment | Hourly | `src/ingest/load_macro.py` |
| BOK ECOS | USD/KRW, KOSPI, KOSDAQ, Korea base rate | Hourly | `src/ingest/load_macro.py` |
| Naver Finance | Daily close prices for 10 major Korean stocks (Samsung Electronics, SK Hynix, Hyundai Motor, Kia, LG Energy Solution, POSCO Holdings, Samsung SDI, Kakao, Naver, Celltrion) | On verification | `src/pipeline/prediction_verifier.py` |
| DART | Corporate disclosure filings (사업보고서, 주요사항보고서, M&A, etc.) via RSS | Every 10 min, 8–18h | `src/pipeline/dart_collector.py` |
| Fed / BOK RSS | Central bank press releases, rate decisions, monetary policy | Every 30 min | `src/pipeline/news_collector.py` |
| Google News | Geopolitical events — sanctions, tariffs, trade war keywords | Every 30 min | `src/pipeline/news_collector.py` |

Additional API keys supported in `config/settings.py` for future data sources: BLS (US labor statistics), MOLIT (Korea real estate), KOSIS (Korea trade statistics).

All macro data feeds into **prediction verification context** — monthly aggregates + recent 30-day daily values provided to Claude Haiku as evidence for judging predictions.

---

## Search Infrastructure

Hybrid BM25 + vector search serves as the retrieval backbone for context assembly and the eval pipeline.

Query embeddings use `intfloat/multilingual-e5-large` (1024-dim) — the same model used to index the production DB. Results reflect actual retrieval quality against real query texts.

```bash
python -m src.search.experiment --mode ablation --dataset eval_data/gold_extended.json --k 5
```

**Alpha ablation** — N=200 queries, K=5:

<!-- AUTO:alpha_ablation -->
| α (BM25 weight) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| α=0.0 | 0.20 | 0.99 | 0.99 |
| α=0.2 | 0.20 | 0.99 | 0.99 |
| α=0.3 | 0.20 | 0.99 | 0.99 |
| α=0.4 | 0.20 | 0.99 | 0.99 |
| **α=0.6** ★ | **0.20** | **1.00** | **0.97** |
| α=0.8 | 0.20 | 0.99 | 0.96 |
| α=1.0 | 0.20 | 0.98 | 0.94 |

Best α=0.6 across all metrics (Precision@5=0.20).

```bash
python -m src.search.experiment --mode ablation --k 5
```
<!-- END:alpha_ablation -->

Embeddings: `intfloat/multilingual-e5-large` (1024-dim, same model as DB indexing), N=200 queries from `gold_extended.json`.

**Key observations**:
- Hybrid (α=0.4–0.6) outperforms both pure methods: +4.8% Recall and +3.0% MRR vs. vector-only
- α=0.6 peaks across all three metrics — slightly more BM25-weight than the production default of 0.4
- Pure BM25 (α=1.0) outperforms pure vector on MRR (0.935 vs 0.908): Korean finance text rewards exact keyword matching (specific tickers, rates, dates) more than semantic paraphrasing alone

**Why they differ**

- **Vector** excels at semantic paraphrasing — "rate hike hurts real estate" ↔ "property values are inversely correlated with interest rates" — but dilutes rare keywords like "4.6%", "30Y", "SVB" in embedding space
- **BM25** pinpoints specific numbers and proper nouns, but fails on synonyms and varied phrasing
- **RRF** combines both: each method covers the blind spots of the other

---

## Eval Pipeline

```bash
# Retrieval only — no Claude calls, fast
python -m src.eval.eval_runner --mode retrieval_only --k 5

# Full — Retrieval + LLM Judge
python -m src.eval.eval_runner --mode full --k 5
```

**Retrieval results** (gold\_extended.json, 200 queries, K=5, hybrid α=0.6):

| Metric | Score |
|--------|-------|
| Precision@5 | 0.199 |
| Recall@5 | 0.995 |
| MRR | 0.938 |

Embeddings: `intfloat/multilingual-e5-large` (same model as DB indexing). Each query has 1 relevant insight in the gold set, so P@5 ceiling = 0.20.

**LLM Judge metrics** (Claude Sonnet as judge, 1–5 scale normalised to 0–1):

| Metric | Description |
|--------|-------------|
| Context Relevance | Retrieved context vs. query relevance |
| Faithfulness | Analysis grounded in sources (inverse hallucination) |
| Answer Relevance | Analysis answers the original question |

---

## Tech Stack

<!-- AUTO:tech_stack -->
| Layer | Technology |
|-------|------------|
| LLM (analysis · agent) | `claude-sonnet-4-6` |
| LLM (extraction · verification) | `claude-haiku-4-5-20251001` |
| Batch API | Anthropic Batch API |
| Embeddings | Vertex AI `text-multilingual-embedding-002` (1024 dims) |
| Vector DB | Cloud SQL PostgreSQL 16 + pgvector (HNSW index) |
| Keyword Search | rank-bm25 + kiwipiepy (Korean morphological analysis) |
| Hybrid Fusion | Reciprocal Rank Fusion (RRF, α=0.4) |
| Scheduler | GCP Cloud Scheduler + Cloud Run Jobs |
| Delivery | python-telegram-bot 21 |
| Data Sources | FRED, BOK ECOS, BLS, MOLIT, DART, Naver Finance |
| Dashboard | Streamlit (Cloud Run Service) |
| Infra | GCP Cloud Run Jobs + Cloud SQL |
<!-- END:tech_stack -->

---

## Metrics

| Metric | Value |
|--------|-------|
| Processed posts | 2,193 |
| Extracted insights | 25,090 |
| Tracked predictions | 5,368 |
| Insight types | 4 (rule, prediction, evaluation, macro_view) |
| Embedding dimensions | 1024 |
| BM25 index size | 16,126 canonical documents |
| Scheduled jobs | 6 |
| Macro indicators tracked | KOSPI, USD/KRW, WTI, VIX, BTC, US10Y, Fed rate, CPI, unemployment |

---

## Setup

### Prerequisites

**Local dev**
- Docker & Docker Compose
- Anthropic API key
- Telegram bot token

**Production (GCP)**
- GCP account + `gcloud` CLI
- Anthropic API key, Telegram bot token
- See [docs/gcp_setup.md](docs/gcp_setup.md) for full GCP setup

### Local Quick Start

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
python -c "
import asyncio, asyncpg, os
from src.search.bm25_index import BM25Index
from dotenv import load_dotenv
load_dotenv()
async def build():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    idx = BM25Index()
    await idx.build(conn)
    idx.save('data/bm25_cache.pkl')
    await conn.close()
asyncio.run(build())
"

# 5. Run a job once (or start the always-on dispatcher)
python -m scripts.run_job --job mer_check
python -m src.pipeline.event_dispatcher   # always-on mode (local only)
```

### Production Deploy (GCP Cloud Run)

```bash
# Build & push image
IMAGE="asia-northeast3-docker.pkg.dev/YOUR_PROJECT/mer-pipeline/app:latest"
docker build -t $IMAGE . && docker push $IMAGE

# Deploy jobs + scheduler
# → full instructions in docs/gcp_setup.md
```

Cloud Scheduler triggers each Cloud Run Job on its own schedule:

| Job | Schedule |
|-----|----------|
<!-- AUTO:jobs -->
| Job | Schedule |
|-----|----------|
| `mer` | every 5 min |
| `dart` | every 10 min, 8-18h |
| `macro_update` | every 1h |
| `macro_alert` | every 30 min |
| `news` | every 30 min |
| `verify_predictions` | 20:00 |
| `report_daily` | 21:00 |
| `report_weekly` | Sun 21:00 |
| `report_monthly` | last day 21:00 |
| `report_quarterly` | 3/6/9/12 last day 21:00 |
| `report_annual` | 12/31 21:00 |
<!-- END:jobs -->

### Prediction Dashboard

```bash
streamlit run src/dashboard/prediction_dashboard.py   # http://localhost:8501
```

### Run Eval

```bash
python -m src.eval.eval_runner --mode retrieval_only
python -m src.search.experiment --k 5
```

---

## Environment Variables

See [.env.example](.env.example) for all required variables.

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Claude API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `GCP_PROJECT_ID` | GCP project ID (production) |
| `TELEGRAM_TIER1_CHAT_ID` | Telegram channel chat ID |
| `FRED_API_KEY` | FRED economic data (free) |
| `BOK_API_KEY` | Bank of Korea ECOS API (free) |
| `BLS_API_KEY` | US Bureau of Labor Statistics (free) |
| `MOLIT_API_KEY` | Korea real estate data / data.go.kr (free) |

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
│   │   ├── hybrid.py             # RRF fusion (α=0.4)
│   │   └── experiment.py         # A/B comparison: vector vs BM25 vs hybrid
│   ├── eval/                     # Eval Pipeline
│   │   ├── eval_runner.py        # Main runner (--mode retrieval_only | full)
│   │   ├── metrics.py            # Precision@K, Recall@K, MRR
│   │   ├── llm_judge.py          # Context Relevance / Faithfulness / Answer Relevance
│   │   ├── eval_dataset.py       # Gold dataset loader
│   │   └── report.py             # Markdown + JSON report generator
│   ├── extract/
│   │   ├── batch_api.py          # Claude Batch API orchestration
│   │   ├── local_embedder.py     # multilingual-e5-large 1024-dim (default)
│   │   ├── vertex_embedder.py    # Vertex AI embedder (optional, GCP_PROJECT_ID required)
│   │   ├── parse_results.py      # Batch result parsing → DB
│   │   └── realtime_extractor.py # Real-time insight extraction (Haiku)
│   ├── ingest/
│   │   ├── load_posts.py
│   │   └── load_macro.py         # FRED / BOK ECOS macro data collection
│   ├── pipeline/
│   │   ├── event_dispatcher.py   # Main runtime (APScheduler, 6 scheduled jobs)
│   │   ├── mer_monitor.py        # Blog RSS watcher (primary data source)
│   │   ├── dart_collector.py     # DART filings (Korean corporate disclosure)
│   │   ├── news_collector.py     # RSS feeds: Fed · BOK · geopolitics
│   │   ├── context_assembler.py  # RAG context builder
│   │   ├── analysis_generator.py # Claude Sonnet post analysis
│   │   └── prediction_verifier.py # Daily Claude Haiku batch verification
│   ├── delivery/
│   │   ├── telegram_bot.py       # Two-tier Telegram delivery
│   │   └── formatters.py
│   └── dashboard/
│       ├── observability.py      # Cost / latency dashboard (local reference)
│       └── prediction_dashboard.py # Prediction accuracy dashboard
├── demo/
│   ├── app.py                    # Streamlit demo (no API keys needed)
│   └── sample_data.json          # Pre-exported insights dataset
├── eval_data/
│   └── gold_extended.json        # Gold dataset: 200 queries with relevant insight IDs
├── results/                      # Eval reports & experiment outputs
├── scripts/
│   ├── init_db.sql               # PostgreSQL schema
│   ├── run_batch.py              # Batch extraction orchestrator
│   └── migrate_predictions.py    # One-time: mer_insights → mer_predictions backfill
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## License

MIT
