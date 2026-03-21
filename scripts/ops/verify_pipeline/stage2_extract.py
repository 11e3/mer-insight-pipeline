"""Stage 2: 검증 포인트 추출 — Haiku, no search."""

import asyncio
import json
import os

import asyncpg
import anthropic
from dotenv import load_dotenv

from .config import MODEL, OUTPUT_DIR, CostTracker

load_dotenv()

SYSTEM = """\
아래 예측 그룹을 보고, 이 예측들의 CORRECT/INCORRECT를 판정하기 위해
실제로 확인해야 하는 팩트(검증 포인트)를 추출하라.

규칙:
- 같은 사건을 묻는 예측은 하나의 검증 포인트로 합친다
- 검증 포인트는 웹 검색으로 확인 가능한 형태로 작성
- 각 검증 포인트에 해당하는 예측 ID 목록을 포함

출력: JSON만.
{"checkpoints": [
  {
    "cp_id": 1,
    "query": "확인할 팩트를 검색 가능한 형태로",
    "prediction_ids": [101, 205, ...],
    "expected_answer_for_correct": "이게 맞으면 해당 예측들은 CORRECT"
  }
]}
"""

GROUP_BATCH = 50  # 큰 그룹은 50건씩 분할 (200건은 출력 잘림 발생)


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    # 코드블록 제거
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break

    start = text.find("{")
    if start < 0:
        return None

    # 정상 파싱 시도
    end = text.rfind("}") + 1
    if end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # 잘린 JSON 복구: 마지막 완전한 checkpoint 항목까지 자르기
    truncated = text[start:]
    # 마지막 완전한 }를 찾아 배열을 닫기
    last_complete = truncated.rfind("},")
    if last_complete > 0:
        repaired = truncated[:last_complete + 1] + "]}"
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return None


async def run(tracker: CostTracker, dry_run: bool = False) -> list[dict]:
    """검증 포인트 추출. 반환: [{"cp_id": ..., "query": ..., "prediction_ids": [...]}]"""
    groups_path = OUTPUT_DIR / "stage1_groups.json"
    if not groups_path.exists():
        if dry_run:
            print("  stage1 결과 없음 - 5,000건 기준 추정")
            est = 30 * (3000 * 0.80 + 2000 * 4.00) / 1_000_000
            print(f"  예상 비용: ${est:.2f}")
            return []
        raise FileNotFoundError(f"{groups_path} 없음. stage1을 먼저 실행하세요.")
    groups = json.loads(groups_path.read_text(encoding="utf-8"))

    # 예측 텍스트 로드
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    rows = await conn.fetch(
        "SELECT id, prediction_text, target_asset, prediction_date "
        "FROM predictions WHERE is_correct IS NULL"
    )
    await conn.close()
    pred_by_id = {r["id"]: r for r in rows}

    total_calls = sum(
        (len(ids) + GROUP_BATCH - 1) // GROUP_BATCH
        for ids in groups.values()
    )
    print(f"[Stage 2] 검증 포인트 추출: {len(groups)}개 주제, ~{total_calls}회 호출")

    if dry_run:
        est = total_calls * (3000 * 0.80 + 2000 * 4.00) / 1_000_000
        print(f"  예상 비용: ${est:.2f}")
        return []

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    all_checkpoints = []
    cp_counter = 0

    for topic, ids in sorted(groups.items(), key=lambda x: -len(x[1])):
        # 큰 그룹은 분할
        for chunk_start in range(0, len(ids), GROUP_BATCH):
            chunk_ids = ids[chunk_start:chunk_start + GROUP_BATCH]

            lines = []
            for pid in chunk_ids:
                p = pred_by_id.get(pid)
                if not p:
                    continue
                asset = p["target_asset"] or ""
                date_str = p["prediction_date"].isoformat() if p["prediction_date"] else ""
                lines.append(f'{pid}. [{asset}] {p["prediction_text"][:120]} ({date_str})')

            user_msg = f"[주제: {topic}]\n" + "\n".join(lines)

            # 최대 2회 재시도 (0개 반환 시)
            for attempt in range(3):
                resp = await client.messages.create(
                    model=MODEL,
                    max_tokens=8192,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": user_msg}],
                )

                tracker.add("stage2", resp.usage.input_tokens, resp.usage.output_tokens)
                tracker.check_budget()

                parsed = _parse_json(resp.content[0].text)
                if parsed and "checkpoints" in parsed and len(parsed["checkpoints"]) > 0:
                    for cp in parsed["checkpoints"]:
                        cp_counter += 1
                        cp["cp_id"] = cp_counter
                        cp["topic"] = topic
                        all_checkpoints.append(cp)
                    break

                if attempt < 2:
                    print(f"    재시도 {attempt + 1}/2 ({topic}, {len(chunk_ids)}건)")
                    await asyncio.sleep(1)

            cp_count = len(parsed.get("checkpoints", [])) if parsed else 0
            print(f"  {topic} ({len(chunk_ids)}건) → {cp_count}개 검증 포인트")
            await asyncio.sleep(0.3)

    # 저장
    out_path = OUTPUT_DIR / "stage2_checkpoints.json"
    out_path.write_text(
        json.dumps(all_checkpoints, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[Stage 2] 완료: {len(all_checkpoints)}개 검증 포인트")
    print(tracker.summary())

    return all_checkpoints
