"""
README 자동 업데이트 스크립트.

config/settings.py, src/pipeline/event_dispatcher.py를 읽어
README.md / README_KR.md의 <!-- AUTO:section --> 마커 사이 내용을 갱신.

Usage:
    python scripts/update_readme.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def extract_str(text: str, var: str) -> str:
    m = re.search(rf"^{var}\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    return m.group(1) if m else "?"


def extract_int(text: str, var: str) -> str:
    m = re.search(rf"^{var}\s*=\s*(\d+)", text, re.MULTILINE)
    return m.group(1) if m else "?"


def update_section(content: str, section: str, new_body: str) -> str:
    """<!-- AUTO:section --> ... <!-- END:section --> 사이 내용 교체."""
    pattern = rf"<!-- AUTO:{re.escape(section)} -->.*?<!-- END:{re.escape(section)} -->"
    if not re.search(pattern, content, flags=re.DOTALL):
        print(f"  WARNING: section '{section}' not found", file=sys.stderr)
        return content
    replacement = f"<!-- AUTO:{section} -->\n{new_body}\n<!-- END:{section} -->"
    return re.sub(pattern, replacement, content, flags=re.DOTALL)


# ── 테크 스택 ────────────────────────────────────────────────────────────────

def tech_stack_en(settings: str) -> str:
    sonnet = extract_str(settings, "MODEL_SONNET")
    haiku  = extract_str(settings, "MODEL_HAIKU")
    embed  = extract_str(settings, "VERTEX_EMBEDDING_MODEL")
    dim    = extract_int(settings, "EMBEDDING_DIM")
    return (
        "| Layer | Technology |\n"
        "|-------|------------|\n"
        f"| LLM (analysis · agent) | `{sonnet}` |\n"
        f"| LLM (extraction · verification) | `{haiku}` |\n"
        "| Batch API | Anthropic Batch API |\n"
        f"| Embeddings | Vertex AI `{embed}` ({dim} dims) |\n"
        "| Vector DB | Cloud SQL PostgreSQL 16 + pgvector (HNSW index) |\n"
        "| Keyword Search | rank-bm25 + kiwipiepy (Korean morphological analysis) |\n"
        "| Hybrid Fusion | Reciprocal Rank Fusion (RRF, α=0.4) |\n"
        "| Scheduler | GCP Cloud Scheduler + Cloud Run Jobs |\n"
        "| Delivery | python-telegram-bot 21 |\n"
        "| Data Sources | FRED, BOK ECOS, BLS, MOLIT, DART, Naver Finance |\n"
        "| Dashboard | Streamlit (Cloud Run Service) |\n"
        "| Infra | GCP Cloud Run Jobs + Cloud SQL |"
    )


def tech_stack_kr(settings: str) -> str:
    sonnet = extract_str(settings, "MODEL_SONNET")
    haiku  = extract_str(settings, "MODEL_HAIKU")
    embed  = extract_str(settings, "VERTEX_EMBEDDING_MODEL")
    dim    = extract_int(settings, "EMBEDDING_DIM")
    return (
        "| 레이어 | 기술 |\n"
        "|--------|------|\n"
        f"| LLM (분석 · 에이전트) | `{sonnet}` |\n"
        f"| LLM (추출 · 검증) | `{haiku}` |\n"
        "| 배치 API | Anthropic Batch API |\n"
        f"| 임베딩 | Vertex AI `{embed}` ({dim}차원) |\n"
        "| 벡터 DB | Cloud SQL PostgreSQL 16 + pgvector (HNSW 인덱스) |\n"
        "| 키워드 검색 | rank-bm25 + kiwipiepy (한국어 형태소 분석) |\n"
        "| 하이브리드 융합 | Reciprocal Rank Fusion (RRF, α=0.4) |\n"
        "| 스케줄러 | GCP Cloud Scheduler + Cloud Run Jobs |\n"
        "| 전송 | python-telegram-bot 21 |\n"
        "| 데이터 | FRED, 한국은행 ECOS, BLS, 국토부, DART, 네이버 금융 |\n"
        "| 대시보드 | Streamlit (Cloud Run Service) |\n"
        "| 인프라 | GCP Cloud Run Jobs + Cloud SQL |"
    )


# ── 스케줄 잡 파싱 ───────────────────────────────────────────────────────────

def _kwarg(block: str, name: str) -> str | None:
    """add_job 블록에서 name=value 추출. 따옴표 유/무 모두 처리."""
    # 따옴표 있는 경우: "3,6,9,12" or 'mon-fri' → 그대로 추출
    # 따옴표 없는 경우: 숫자/단어만 — 쉼표/공백/닫는괄호에서 끊음
    pattern = rf'(?<!\w){re.escape(name)}=(?:"([^"]+)"|\'([^\']+)\'|([0-9*][0-9*\/\-]*|last|\w+)(?=[,\s\)]))'
    m = re.search(pattern, block)
    if not m:
        return None
    return next(g for g in m.groups() if g is not None)


_DOW_EN = {"mon": "Mon", "tue": "Tue", "wed": "Wed",
           "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
_DOW_KR = {"mon": "매주 월요일", "tue": "매주 화요일", "wed": "매주 수요일",
           "thu": "매주 목요일", "fri": "매주 금요일", "sat": "매주 토요일", "sun": "매주 일요일"}


def _fmt_cron(block: str, dow_map: dict, lang: str = "en") -> str:
    hour   = _kwarg(block, "hour")   or "*"
    minute = _kwarg(block, "minute") or "0"
    dow    = _kwarg(block, "day_of_week")
    month  = _kwarg(block, "month")
    day    = _kwarg(block, "day")

    # minute이 단순 숫자면 2자리로
    if re.fullmatch(r"\d+", minute):
        minute = minute.zfill(2)

    # minute=*/10 + hour range → "every 10 min, hour-range"
    if minute.startswith("*/"):
        interval = minute[2:]
        if lang == "kr":
            return f"매 {interval}분, {hour}시"
        return f"every {interval} min, {hour}h"

    parts: list[str] = []
    if dow:
        d = dow.split("-")[0].lower()
        parts.append(dow_map.get(d, d))

    month_fmt = month.replace(",", "/") if month else None

    if month and day:
        if day == "last":
            if lang == "kr":
                parts.append(f"{month_fmt}월 말일")
            else:
                parts.append(f"{month_fmt} last day")
        else:
            if lang == "kr":
                parts.append(f"{month_fmt}월 {day}일")
            else:
                parts.append(f"{month_fmt}/{day}")
    elif month:
        parts.append(f"{month_fmt}월" if lang == "kr" else f"month={month_fmt}")
    elif day:
        if day == "last":
            parts.append("말일" if lang == "kr" else "last day")
        else:
            parts.append(f"{day}일" if lang == "kr" else f"day {day}")

    parts.append(f"{hour}:{minute}")
    return " ".join(parts)


def parse_jobs(dispatcher: str) -> list[tuple[str, str, str]]:
    """(id, schedule_en, schedule_kr) 목록 반환."""
    blocks = re.findall(r"self\.scheduler\.add_job\((.*?)\)", dispatcher, re.DOTALL)
    jobs: list[tuple[str, str, str]] = []

    for block in blocks:
        id_m  = re.search(r'id=["\']([^"\']+)["\']', block)
        typ_m = re.search(r'"(interval|cron)"', block)
        if not id_m or not typ_m:
            continue

        jid = id_m.group(1)
        typ = typ_m.group(1)

        if typ == "interval":
            mins = _kwarg(block, "minutes")
            hrs  = _kwarg(block, "hours")
            if mins:
                en = f"every {mins} min"
                kr = f"매 {mins}분마다"
            elif hrs:
                en = f"every {hrs}h"
                kr = f"매 {hrs}시간마다"
            else:
                en = kr = "interval"
        else:
            en = _fmt_cron(block, _DOW_EN, lang="en")
            kr = _fmt_cron(block, _DOW_KR, lang="kr")

        jobs.append((jid, en, kr))

    return jobs


def jobs_table_en(jobs: list[tuple]) -> str:
    lines = ["| Job | Schedule |", "|-----|----------|"]
    for jid, en, _ in jobs:
        lines.append(f"| `{jid}` | {en} |")
    return "\n".join(lines)


def jobs_table_kr(jobs: list[tuple]) -> str:
    lines = ["| 잡 | 스케줄 |", "|----|--------|"]
    for jid, _, kr in jobs:
        lines.append(f"| `{jid}` | {kr} |")
    return "\n".join(lines)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    settings   = read("config/settings.py")
    dispatcher = read("src/pipeline/event_dispatcher.py")
    jobs       = parse_jobs(dispatcher)

    updates = [
        ("README.md",    "tech_stack", tech_stack_en(settings)),
        ("README.md",    "jobs",       jobs_table_en(jobs)),
        ("README_KR.md", "tech_stack", tech_stack_kr(settings)),
        ("README_KR.md", "jobs",       jobs_table_kr(jobs)),
    ]

    changed: list[str] = []
    for filename, section, new_body in updates:
        path = ROOT / filename
        original = path.read_text(encoding="utf-8")
        updated  = update_section(original, section, new_body)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(f"{filename}:{section}")

    if changed:
        print("Updated:", ", ".join(changed))
    else:
        print("No changes.")


if __name__ == "__main__":
    main()
