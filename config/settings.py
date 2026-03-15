import os
from dotenv import load_dotenv

load_dotenv()

# ─── DB ───────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]

# ─── Claude API ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU  = "claude-haiku-4-5-20251001"

# ─── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_TIER1_CHAT_ID = os.environ.get("TELEGRAM_TIER1_CHAT_ID", "")

# ─── 외부 API ─────────────────────────────────────────────────────────────────
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
BOK_API_KEY  = os.environ.get("BOK_API_KEY", "")

# ─── 블로그 ──────────────────────────────────────────────────────────────────
BLOG_ID  = "ranto28"
BLOG_RSS = f"https://rss.blog.naver.com/{BLOG_ID}.xml"

# ─── 매크로 임계값 (급변 알림) ────────────────────────────────────────────────
MACRO_ALERT_THRESHOLDS = {
    "kospi":          0.02,
    "usd_krw":        0.015,
    "vix":            0.15,
    "btc_usd":        0.05,
    "wti":            0.05,
    "us_cpi_yoy":     0.003,   # CPI 0.3%p 이상 변화
    "us_unemployment": 0.002,  # 실업률 0.2%p 이상 변화
}

# ─── GCP ──────────────────────────────────────────────────────────────────────
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_LOCATION   = os.environ.get("GCP_LOCATION", "us-central1")

# ─── 임베딩 ───────────────────────────────────────────────────────────────────
VERTEX_EMBEDDING_MODEL = "text-multilingual-embedding-002"
LOCAL_EMBEDDING_MODEL  = "intfloat/multilingual-e5-large"
EMBEDDING_DIM          = 1024   # DB 실제 차원 (intfloat/multilingual-e5-large)
EMBEDDING_BATCH_SIZE   = 32

# ─── 데이터 경로 ──────────────────────────────────────────────────────────────
DATA_DIR     = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_JSON_DIR = os.path.join(DATA_DIR, "raw", "json")

# ─── RSS 소스 URL ─────────────────────────────────────────────────────────────
FED_RSS_URL   = "https://www.federalreserve.gov/feeds/press_all.xml"
BOK_RSS_URL   = "https://www.bok.or.kr/portal/bbs/B0000011/rss.xml?menuNo=200059"
NEWS_RSS_URLS = [
    "https://news.google.com/rss/search?q=FOMC+금리+연준&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=한국은행+금통위+기준금리&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=geopolitics+sanctions+trade+war&hl=en&gl=US&ceid=US:en",
]
