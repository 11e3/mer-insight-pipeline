import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ─── DB ───────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]

# ─── Claude API ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU  = "claude-haiku-4-5-20251001"
MODEL_OPUS   = "claude-opus-4-6"

# ─── 블로그 ──────────────────────────────────────────────────────────────────
BLOG_ID  = "ranto28"
BLOG_RSS = f"https://rss.blog.naver.com/{BLOG_ID}.xml"

# ─── GCP ──────────────────────────────────────────────────────────────────────
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_LOCATION   = os.environ.get("GCP_LOCATION", "us-central1")

# ─── 임베딩 ───────────────────────────────────────────────────────────────────
VERTEX_EMBEDDING_MODEL = "text-multilingual-embedding-002"
LOCAL_EMBEDDING_MODEL  = "intfloat/multilingual-e5-large"
EMBEDDING_DIM          = 1024   # DB 실제 차원 (intfloat/multilingual-e5-large)
EMBEDDING_BATCH_SIZE   = 32

# ─── 데이터 경로 ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR      = str(_PROJECT_ROOT / "data")
RAW_JSON_DIR  = str(_PROJECT_ROOT / "data" / "raw" / "json")
