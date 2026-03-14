"""
채널별 메시지 포맷터 (Markdown v1 모드)
"""

import re

from src.pipeline.event_types import EventType


def format_message(
    event_type: EventType,
    event: dict,
    analysis: str,
    rules_count: int = 0,
    enriched: dict | None = None,
) -> str:
    body = _to_md1(analysis)
    if event_type == EventType.MER_NEW_POST:
        return _fmt_mer_post(event, body, enriched)
    elif event_type == EventType.MACRO_ALERT:
        return _fmt_macro_alert(event, body)
    else:
        return _fmt_full_analysis(event, body)


def _fmt_mer_post(event: dict, analysis: str, enriched: dict | None = None) -> str:
    title = _esc(event.get("title", ""))
    url   = event.get("source", "")

    parts = [
        f"📝 *메르 새 글*\n_{title}_\n",
        analysis,
    ]

    if enriched:
        # 2. 현재 시장 스냅샷
        snap = enriched.get("market_snapshot")
        if snap:
            parts.append(
                f"\n📊 *현재 시장* ({snap['date']})\n"
                f"KOSPI {snap['kospi']} | USD/KRW {snap['usd_krw']} | "
                f"WTI {snap['wti']} | VIX {snap['vix']} | US10Y {snap['us_10y']}"
            )

        # 4. 행동 시그널
        signal = enriched.get("action_signal")
        if signal:
            parts.append(f"\n⚡ *행동 시그널*\n{signal}")

        # 3. 메르의 과거 포지션
        past = enriched.get("past_position")
        if past:
            parts.append(f"\n🕰 *메르의 과거 시각*\n_{_esc(past)}_")

        # 1. 관련 과거 메르 글
        related = enriched.get("related_posts")
        if related:
            links = "\n".join(
                f"• [{r['date']}] [{_esc(r['title'][:35])}]({r['url']}) _{r['similarity']}%_"
                for r in related
            )
            parts.append(f"\n📌 *관련 과거 글*\n{links}")

        # 5. 유사 이슈 후 시장 반응
        react = enriched.get("market_reaction")
        if react and react.get("n", 0) >= 2:
            d3 = react["d3"]
            d7 = react["d7"]
            parts.append(
                f"\n📈 *유사 이슈 후 시장 반응* (과거 {react['n']}회 평균)\n"
                f"3일: KOSPI {d3['kospi']} | WTI {d3['wti']} | USD/KRW {d3['usd_krw']}\n"
                f"7일: KOSPI {d7['kospi']} | WTI {d7['wti']} | USD/KRW {d7['usd_krw']}"
            )

    parts.append(f"\n🔗 [원문 읽기]({url})")
    return "\n".join(parts)


def _fmt_full_analysis(event: dict, analysis: str) -> str:
    title = _esc(event.get("title", ""))
    return (
        f"🤖 *메르 프레임워크 AI 분석*\n"
        f"📊 _{title}_\n\n"
        f"{analysis}"
    )


def _fmt_macro_alert(event: dict, analysis: str) -> str:
    title = _esc(event.get("title", ""))
    return f"🚨 *매크로 알림*\n_{title}_\n\n{analysis}"


def format_short_post(post: dict, topic: str, summary: str) -> str:
    """비경제 포스팅용 짧은 포맷."""
    title = _esc(post.get("title", ""))
    url   = post.get("source") or post.get("url", "")
    body  = summary or "(요약 없음)"
    return (
        f"📝 *메르 새 글* _({topic})_\n"
        f"_{title}_\n\n"
        f"{body}\n\n"
        f"💤 _경제/시장 관련 내용 없음_\n"
        f"🔗 [원문 읽기]({url})"
    )


def _esc(text: str) -> str:
    """Markdown v1에서 문제가 되는 특수문자 이스케이프."""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")


def _to_md1(text: str) -> str:
    """Claude 마크다운 출력 → 텔레그램 Markdown v1 호환 변환."""
    # 마크다운 표 제거 → 목록으로 전환
    text = re.sub(r"\|[-:| ]+\|\n?", "", text)
    text = re.sub(r"\|(.+)\|", lambda m: "\n".join(
        f"- {c.strip()}" for c in m.group(1).split("|") if c.strip()
    ), text)
    # ### 헤더 → 볼드
    text = re.sub(r"^#{1,3} (.+)$", r"*\1*", text, flags=re.MULTILINE)
    # **bold** → *bold*  (v1은 단일 *)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # --- 구분선 제거
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    # > 인용부호 제거
    text = re.sub(r"^> ?", "", text, flags=re.MULTILINE)
    # 연속 빈줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
