"""
PostEnricher — 메르 새 글 알림에 부가 컨텍스트를 추가한다.

1. 관련 과거 메르 글
2. 현재 시장 스냅샷
3. 메르의 과거 포지션 (유사 주제)
4. 행동 시그널 추출
5. 유사 이슈 후 시장 반응 통계
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY
from src.extract.vertex_embedder import VertexEmbedder

log = logging.getLogger(__name__)


class PostEnricher:
    def __init__(self, conn: asyncpg.Connection, embedder: VertexEmbedder):
        self.conn = conn
        self.embedder = embedder
        self._claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    async def enrich(self, post: dict, analysis: str) -> dict:
        """5개 부가정보를 비동기로 수집해 반환."""
        results = await asyncio.gather(
            self._related_posts(post),
            self._market_snapshot(),
            self._past_position(post),
            self._action_signal(analysis),
            self._market_reaction(post),
            return_exceptions=True,
        )
        keys = ["related_posts", "market_snapshot", "past_position", "action_signal", "market_reaction"]
        return {k: (v if not isinstance(v, Exception) else None) for k, v in zip(keys, results)}

    # ── 1. 관련 과거 메르 글 ────────────────────────────────────────────────

    async def _related_posts(self, post: dict) -> list[dict]:
        """벡터 검색으로 유사한 과거 메르 글 2~3개 반환."""
        text = f"{post['title']} {post.get('content_text', '')[:300]}"
        vecs = await self.embedder.embed_passages([text])
        q = vecs[0]
        vec_str = f"[{','.join(str(x) for x in q)}]"

        rows = await self.conn.fetch("""
            SELECT e.id, e.title, e.event_date, e.source,
                   1 - (e.embedding <=> $1::vector) AS similarity
            FROM events e
            WHERE e.event_type = 'mer_new_post'
              AND e.source != $2
              AND e.embedding IS NOT NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT 5
        """, vec_str, post.get("url", ""))

        return [
            {
                "title": r["title"],
                "date": r["event_date"].strftime("%Y-%m-%d") if r["event_date"] else "",
                "url": r["source"] or "",
                "similarity": round(r["similarity"] * 100),
            }
            for r in rows
            if r["similarity"] > 0.6
        ][:3]

    # ── 2. 현재 시장 스냅샷 ────────────────────────────────────────────────

    async def _market_snapshot(self) -> Optional[dict]:
        """macro_daily 최신 행에서 주요 지표 반환."""
        row = await self.conn.fetchrow("""
            SELECT date, kospi, usd_krw, wti, vix, us_10y
            FROM macro_daily
            ORDER BY date DESC
            LIMIT 1
        """)
        if not row:
            return None
        return {
            "date": row["date"].strftime("%m/%d"),
            "kospi": f"{row['kospi']:,.0f}" if row["kospi"] else "-",
            "usd_krw": f"{row['usd_krw']:,.0f}" if row["usd_krw"] else "-",
            "wti": f"${row['wti']:.1f}" if row["wti"] else "-",
            "vix": f"{row['vix']:.1f}" if row["vix"] else "-",
            "us_10y": f"{row['us_10y']:.2f}%" if row["us_10y"] else "-",
        }

    # ── 3. 메르의 과거 포지션 ──────────────────────────────────────────────

    async def _past_position(self, post: dict) -> Optional[str]:
        """유사 주제 과거 분석에서 메르의 핵심 시각을 1~2문장으로 요약."""
        text = f"{post['title']} {post.get('content_text', '')[:300]}"
        vecs = await self.embedder.embed_passages([text])
        vec_str = f"[{','.join(str(x) for x in vecs[0])}]"

        rows = await self.conn.fetch("""
            SELECT aa.analysis_text, e.event_date
            FROM auto_analyses aa
            JOIN events e ON e.id = aa.event_id
            WHERE e.event_type = 'mer_new_post'
              AND e.source != $2
              AND e.embedding IS NOT NULL
              AND aa.analysis_text IS NOT NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT 3
        """, vec_str, post.get("url", ""))

        if not rows:
            return None

        combined = "\n\n---\n\n".join(
            f"[{r['event_date'].strftime('%Y-%m-%d')}]\n{r['analysis_text'][:600]}"
            for r in rows
        )

        resp = self._claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system="당신은 메르(경제 블로거)의 과거 글들을 읽고 해당 주제에 대한 핵심 시각을 1~2문장으로 요약하는 역할입니다. 간결하게, 한국어로만 답하세요.",
            messages=[{
                "role": "user",
                "content": f"현재 글 주제: {post['title']}\n\n과거 유사 분석들:\n{combined}\n\n→ 이 주제에 대한 메르의 핵심 시각을 1~2문장으로 요약해주세요.",
            }],
        )
        return resp.content[0].text.strip()

    # ── 4. 행동 시그널 ────────────────────────────────────────────────────

    async def _action_signal(self, analysis: str) -> Optional[str]:
        """분석 텍스트에서 행동 시그널(매수/매도/관망/주시 등)을 추출."""
        resp = self._claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            system="투자 분석 텍스트에서 행동 시그널을 추출합니다. '관망', '매수 검토', '리스크 주시', '단기 수혜' 같은 키워드 3~5개를 콤마로 구분해 한 줄로만 답하세요. 분석이 없으면 '시그널 없음'.",
            messages=[{"role": "user", "content": analysis[:2000]}],
        )
        signal = resp.content[0].text.strip()
        return signal if signal != "시그널 없음" else None

    # ── 5. 유사 이슈 후 시장 반응 통계 ────────────────────────────────────

    async def _market_reaction(self, post: dict) -> Optional[dict]:
        """유사한 과거 글 이후 3일/7일 시장 변화율 평균."""
        text = f"{post['title']} {post.get('content_text', '')[:300]}"
        vecs = await self.embedder.embed_passages([text])
        vec_str = f"[{','.join(str(x) for x in vecs[0])}]"

        rows = await self.conn.fetch("""
            SELECT e.event_date
            FROM events e
            WHERE e.event_type = 'mer_new_post'
              AND e.source != $2
              AND e.embedding IS NOT NULL
              AND 1 - (e.embedding <=> $1::vector) > 0.65
            ORDER BY e.embedding <=> $1::vector
            LIMIT 5
        """, vec_str, post.get("url", ""))

        if not rows:
            return None

        changes_3d: list[dict] = []
        changes_7d: list[dict] = []

        for r in rows:
            event_date: date = r["event_date"].date() if hasattr(r["event_date"], "date") else r["event_date"]

            base = await self.conn.fetchrow("""
                SELECT kospi, usd_krw, wti
                FROM macro_daily WHERE date <= $1 ORDER BY date DESC LIMIT 1
            """, event_date)

            d3 = await self.conn.fetchrow("""
                SELECT kospi, usd_krw, wti
                FROM macro_daily WHERE date >= $1 ORDER BY date ASC LIMIT 1
            """, event_date + timedelta(days=3))

            d7 = await self.conn.fetchrow("""
                SELECT kospi, usd_krw, wti
                FROM macro_daily WHERE date >= $1 ORDER BY date ASC LIMIT 1
            """, event_date + timedelta(days=7))

            if base and d3 and base["kospi"] and d3["kospi"]:
                changes_3d.append({
                    "kospi": (d3["kospi"] - base["kospi"]) / base["kospi"] * 100,
                    "usd_krw": (d3["usd_krw"] - base["usd_krw"]) / base["usd_krw"] * 100 if base["usd_krw"] and d3["usd_krw"] else 0,
                    "wti": (d3["wti"] - base["wti"]) / base["wti"] * 100 if base["wti"] and d3["wti"] else 0,
                })
            if base and d7 and base["kospi"] and d7["kospi"]:
                changes_7d.append({
                    "kospi": (d7["kospi"] - base["kospi"]) / base["kospi"] * 100,
                    "usd_krw": (d7["usd_krw"] - base["usd_krw"]) / base["usd_krw"] * 100 if base["usd_krw"] and d7["usd_krw"] else 0,
                    "wti": (d7["wti"] - base["wti"]) / base["wti"] * 100 if base["wti"] and d7["wti"] else 0,
                })

        def avg(lst, key):
            vals = [x[key] for x in lst if x.get(key) is not None]
            return sum(vals) / len(vals) if vals else None

        def fmt(v):
            if v is None:
                return "-"
            sign = "+" if v >= 0 else ""
            return f"{sign}{v:.1f}%"

        return {
            "n": len(rows),
            "d3": {k: fmt(avg(changes_3d, k)) for k in ["kospi", "usd_krw", "wti"]},
            "d7": {k: fmt(avg(changes_7d, k)) for k in ["kospi", "usd_krw", "wti"]},
        }
