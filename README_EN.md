# mer-insight-pipeline

[![CI](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml/badge.svg)](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml)
[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg?token=WEE9EGD2QP)](https://codecov.io/gh/11e3/mer-insight-pipeline)

**mer-insight-pipeline** turns unstructured Korean financial blog prose into structured, time-bound predictions — then tracks whether they actually come true.

Korean economic commentary doesn't come with tickers, dates, or confidence levels. Extracting verifiable predictions from natural language, assigning temporal bounds, and fact-checking outcomes against real-world events is a non-trivial NLP + information retrieval problem that no off-the-shelf tool solves. This pipeline is a solo-built, end-to-end system.

The pipeline monitors [Mer (ranto28)](https://blog.naver.com/ranto28)'s finance blog, extracts predictions via Claude Batch API, and verifies each against real outcomes — 5,010 predictions tracked, re-verification in progress. Retrieval is powered by hybrid BM25 + pgvector search (25,090 indexed insights, RRF fusion at α=0.6) on PostgreSQL with no vector-DB vendor lock-in.

[한국어 README](README.md) · **[📊 Live Dashboard](https://mer-insight-pipeline.streamlit.app/)**

### What I Built (Solo)

- **Full pipeline**: scraping → LLM extraction → embedding → hybrid search → verification → dashboard
- **Data-driven decisions**: ran ablation experiments on search, automated verification experiments that proved manual-only is the right call

---

## Architecture

```mermaid
flowchart TD
    subgraph Batch Extraction
        A[Naver Blog<br>2,223 posts] -->|scrape| B[parse_results.py]
        B -->|rules / predictions<br>evaluations / macro_views| C[(PostgreSQL<br>+ pgvector)]
        C --> EMB[embed/local.py<br>1024-dim vectors]
        EMB --> C
    end

    subgraph "Daily Pipeline (01:00)"
        ED[event_dispatcher.py] -->|1| MER[collect/mer_monitor<br>new posts]
        ED -->|2| PV[verify/verifier<br>export + notify]
        MER --> C
        PV -->|pending export| EX[manual_verify/pending/]
        EX -->|claude.ai| MAN[Manual Verification]
        MAN -->|import_manual_verdicts.py| C
    end

    subgraph Hybrid Search
        HS1[BM25<br>kiwipiepy] & HS2[pgvector<br>HNSW] -->|RRF α=0.6| HS3[HybridSearcher]
    end

    HS3 --> C

    C --> Q[Streamlit Dashboard]

    EV[eval/experiment.py<br>offline ablation] -.-> HS3
```

---

## Prediction Verification

Every `prediction`-type insight extracted from Mer's posts is stored in `mer_predictions`. Predictions with `expected_date` in the past are exported for **manual verification via claude.ai**, then imported back into the DB.

**Verdicts**

| Verdict | Condition |
|---------|-----------|
| `CORRECT` | Predicted outcome confirmed with evidence |
| `INCORRECT` | Predicted outcome contradicted by evidence |
| `PENDING` | Condition not yet met — re-exported when `expected_date` passes |

### Workflow

1. **Daily pipeline** exports verifiable predictions → `data/manual_verify/pending/`
2. **Telegram alert** notifies when new predictions are ready
3. **Manual verification** via claude.ai (Opus 4.6 with web search)
4. **Import results** via `python scripts/ops/import_manual_verdicts.py`

### Why Not Fully Automated?

We ran extensive experiments comparing automated API verification against manual claude.ai verification on 77 predictions. **No automated approach achieved acceptable accuracy:**

| Approach | Match Rate | Verdict Flips | Cost/pred | Notes |
|----------|-----------|---------------|-----------|-------|
| API only (no search) | 16.9% | 1 | $0.01 | 80% PENDING — model lacks post-cutoff knowledge |
| API + one-shot Brave Search | 37.7% | 5 | $0.02 | Snippets insufficient for fact-checking |
| API + built-in web_search (Sonnet) | 30% | 2 | $0.05 | Better search quality, still unreliable |
| API + agentic tool_use (Opus) | 40% | 3 | $0.26 | Token accumulation makes cost prohibitive |
| **claude.ai manual (Opus)** | **100%** | **0** | **$0** | **Subscription model, best quality** |

**Root cause:** Prediction verification is a fact-checking problem requiring real-time information. The API cannot reliably access or interpret current events, and verdict flips (CORRECT↔INCORRECT) make even screening-level automation dangerous — wrong verdicts would pollute the database.

**Conclusion:** Manual verification via claude.ai remains the only reliable method. The pipeline automates everything else: export, batching, notification, and import. Batch size is limited to 20 predictions to prevent ID shifting, and source_url is required for all verdicts.

### Data Quality Audit

Large-batch verification (50–100+ predictions) caused ID-verdict misalignment in LLM JSON output, corrupting the database. A blind audit of 50 random samples revealed **36% contamination rate** (95% CI: 24–50%). All verdicts were reset and the verification process was redesigned with small batches (20) and mandatory source URLs.

| Audit Item | Result |
|------------|--------|
| Sample size | 50 (random, blind) |
| Match (same verdict) | 32 (64%) |
| Verdict flip (CORRECT↔INCORRECT) | 7 (14%) |
| Changed to PENDING | 11 (22%) |
| 95% CI (Wilson) | Contamination 24.1%–49.9% |

### 3-Stage Compressed Verification Pipeline

Verifying 5,000 predictions one-by-one costs ~$142 (Haiku). Instead, we group predictions by topic, extract unique verification checkpoints, and search once per checkpoint.

```
5,020 predictions → 16 topics → 223 checkpoints → web_search → 302 predictions mapped
```

| Stage | Action | API Calls | Cost |
|-------|--------|-----------|------|
| Stage 1 | Topic classification (Haiku, no search) | 51 | $0.66 |
| Stage 2 | Checkpoint extraction (Haiku, no search) | 31 | $1.22 |
| Stage 3 | Search + verdict (Haiku + web_search) | 223 | $10.21 |
| Stage 4 | Map to individual predictions (local) | 0 | $0 |
| web_search billing | ~446 searches × ~$0.02 | - | ~$9 |
| **Total** | | **305** | **~$21** |

**Result:** 136 CONFIRMED, 61 DENIED, 26 UNKNOWN checkpoints. 302 predictions mapped (178 CORRECT, 89 INCORRECT, 35 PENDING).

**Limitation:** Stage 2 failed to extract checkpoints for 11 topics (4,718 predictions) due to Haiku output length limits on 200-item batches. Reducible by smaller batch sizes (200→50) with additional budget.

**Current Stats**

| Status | Count |
|--------|-------|
| Draft CORRECT | 178 |
| Draft INCORRECT | 89 |
| PENDING | 4,753 |
| **Total** | **5,020** |

*Draft verdicts pending human review before DB import. source_url required.*

---

## Search Infrastructure

Hybrid BM25 + vector search is the retrieval layer that feeds the verification pipeline. When a prediction comes due for verification, the searcher retrieves the most relevant insights and context from 25,090 indexed documents — **missing a relevant document means a prediction could be verified with incomplete evidence.** That's why Recall matters more than ranking precision here.

Query embeddings use `intfloat/multilingual-e5-large` (1024-dim) — the same model used to index the production DB.

**Alpha ablation** — N=200 queries, K=5:

| α (BM25 weight) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| **α=0.0** | 0.199 | 0.995 | **0.995** |
| **α=0.6** ★ | **0.200** | **1.000** | 0.968 |
| α=1.0 | 0.196 | 0.980 | 0.935 |

Production default: **α=0.6** — the only setting that achieves perfect Recall (1.000). Vector-only (α=0.0) has the best MRR but misses 0.5% of relevant documents; for a verification pipeline where one missed fact can flip a verdict, that gap matters.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| LLM (extraction) | `claude-sonnet-4-6` (Haiku optional via `--haiku`) |
| LLM (verification) | claude.ai (Opus 4.6, manual) |
| Batch API | Anthropic Batch API |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim, local) |
| Vector DB | PostgreSQL 16 + pgvector (HNSW index) |
| Keyword Search | rank-bm25 + kiwipiepy (Korean morphological analysis) |
| Hybrid Fusion | Reciprocal Rank Fusion (RRF, α=0.6) |
| Scheduler | APScheduler (local) / GCP Cloud Scheduler + Cloud Run Job |
| Dashboard | Streamlit |

---

## Metrics

| Metric | Value |
|--------|-------|
| Processed posts | 2,223 |
| Extracted insights | 25,090 |
| Tracked predictions | 5,020 |
| Verified predictions | Re-verification in progress (post-reset) |
| Insight types | 4 (rule, prediction, evaluation, macro_view) |
| Embedding dimensions | 1024 |

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

# 2. Start the database
docker compose up -d db

# 3. Run batch extraction (posts → insights)
python scripts/run_batch.py all

# 4. Build BM25 index cache
python -m src.search.bm25_index

# 5. Run pipeline once (or start the daily scheduler)
python -m scripts.run_job                 # run once
python -m src.pipeline.event_dispatcher   # daily 01:00 scheduler
```

### Daily Pipeline

Runs once daily at 01:00 (KST) via Cloud Scheduler or APScheduler:

1. Mer blog — check for new posts, extract predictions
2. Prediction export — export verifiable predictions + Telegram alert

### Dashboard

```bash
streamlit run src/dashboard/app.py
```

### Eval

```bash
python -m src.eval.eval_runner --mode retrieval_only
python -m src.eval.experiment --mode ablation --k 5
```

---

## Tests

```bash
# Unit tests (no DB required)
pytest tests/ -v

# Integration tests (requires PostgreSQL)
TEST_DATABASE_URL=postgresql://mer:pass@localhost:5432/mer_test \
  pytest tests/test_integration_dispatcher.py -v
```

Unit tests run without a database. Integration tests require `TEST_DATABASE_URL` — they are automatically skipped when the variable is not set.

---

## Cost

Prediction verification is manual (via claude.ai subscription, ~$20/month). The only API cost is real-time insight extraction when new posts are detected.

| Component | Cost | Frequency |
|-----------|------|-----------|
| Insight extraction (Sonnet) | ~$0.01/post | Per new post |
| Prediction verification | $0 (claude.ai subscription) | Weekly batch |
| Embedding (local) | $0 | Per new insight |

**Estimated monthly cost ≈ $2-5** (extraction only). Verification is covered by claude.ai subscription.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✓ | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | ✓ | Claude API key |
| `GCP_PROJECT_ID` | optional | GCP project ID (for Vertex AI embeddings) |
| `GCP_LOCATION` | optional | Vertex AI region (default: us-central1) |

---

## Project Structure

```
mer-insight-pipeline/
├── src/
│   ├── config/                     # Settings & Prompts
│   │   ├── settings.py             # All configuration, loaded from .env
│   │   └── prompts.py              # Claude extraction prompts
│   ├── db/                         # Shared DB Utilities
│   │   └── connection.py           # connect(), get_pool() context managers
│   ├── embed/                      # Embedding
│   │   ├── protocol.py             # Embedder Protocol interface
│   │   ├── local.py                # multilingual-e5-large 1024-dim (default)
│   │   ├── vertex.py               # Vertex AI 768-dim (GCP only)
│   │   ├── factory.py              # get_embedder() factory + vec_str()
│   │   └── backfill.py             # Batch fill NULL embeddings
│   ├── extract/                    # Insight Extraction
│   │   ├── batch_api.py            # Claude Batch API orchestration
│   │   ├── parse_results.py        # Batch result JSONL → DB
│   │   └── realtime.py             # Real-time insight extraction (Haiku)
│   ├── collect/                    # Data Collection
│   │   ├── mer_monitor.py          # Blog RSS watcher
│   │   ├── posts.py                # JSON → mer_posts bulk loader
│   │   └── date_parser.py          # Korean date string parser
│   ├── verify/                     # Prediction Verification
│   │   ├── verifier.py             # Export verifiable predictions + Telegram notify
│   │   └── prompt.py               # Constants (batch size)
│   ├── search/                     # Hybrid Search
│   │   ├── bm25_index.py           # BM25 with kiwipiepy + pickle cache
│   │   ├── vector_index.py         # pgvector HNSW wrapper (1024-dim)
│   │   └── hybrid.py               # RRF fusion (α=0.6)
│   ├── eval/                       # Eval Pipeline
│   │   ├── experiment.py           # Ablation: vector vs BM25 vs hybrid
│   │   ├── eval_runner.py          # Main runner (--mode retrieval_only | full)
│   │   ├── metrics.py              # Precision@K, Recall@K, MRR
│   │   ├── llm_judge.py            # LLM-as-judge (Claude Sonnet)
│   │   └── report.py               # Markdown + JSON report generator
│   ├── pipeline/                   # Orchestration
│   │   └── event_dispatcher.py     # APScheduler / Cloud Run Job entry
│   └── dashboard/                  # Streamlit Dashboard
│       ├── app.py                  # Layout & rendering
│       ├── queries.py              # DB query functions
│       └── topics.py               # Topic classification (TOPIC_KEYWORDS)
├── scripts/
│   ├── run_job.py                  # Cloud Run Job / local pipeline entry
│   ├── run_batch.py                # Batch extraction orchestrator
│   ├── reembed_all.py              # Full re-embedding (model swap)
│   ├── naver_blog_scraper.py       # Blog scraper
│   ├── ops/                        # Data operation scripts
│   │   ├── export_*.py             # Prediction export (manual_verify, rounds)
│   │   ├── import_*.py             # Manual verdict import
│   │   ├── fill_*.py               # Field backfill (expected_date, etc.)
│   │   ├── migrate_predictions.py  # One-time backfill: insights → predictions
│   │   ├── populate_topics.py      # Batch topic classification
│   │   ├── cluster_insights.py     # DBSCAN deduplication
│   │   └── regroup_by_topic.py     # Topic re-classification
│   └── eval/                       # Eval scripts
│       ├── expand_eval_dataset.py  # Auto-expand gold dataset
│       └── compare_judges.py       # Judge comparison
├── eval_data/
│   └── gold_extended.json          # Gold dataset: 200 queries with relevant IDs
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## License

MIT
