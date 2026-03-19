"""Stage 4: 검증 포인트 verdict → 개별 예측 draft verdict 매핑. API 호출 없음."""

import json

from .config import OUTPUT_DIR


def run() -> dict:
    """stage3 결과를 개별 예측에 매핑. 반환: {pred_id: {...draft...}}"""
    # stage3 결과 로드
    results_path = OUTPUT_DIR / "stage3_results.json"
    raw = json.loads(results_path.read_text(encoding="utf-8"))

    # incomplete 플래그 처리
    if isinstance(raw, dict) and "results" in raw:
        results = raw["results"]
        print(f"  ⚠️ stage3 incomplete — {len(results)}건만 매핑")
    else:
        results = raw

    drafts: dict[int, dict] = {}

    for r in results:
        cp_verdict = r.get("verdict", "UNKNOWN")
        source_url = r.get("source_url", "")
        fact = r.get("fact_found", "")
        cp_id = r.get("cp_id", 0)
        expected = r.get("expected_answer_for_correct", "")

        # CONFIRMED → expected_answer가 맞았으므로 해당 예측들은 CORRECT
        # DENIED → expected_answer가 틀렸으므로 해당 예측들은 INCORRECT
        # UNKNOWN → PENDING 유지
        if cp_verdict == "CONFIRMED":
            draft_verdict = "CORRECT"
        elif cp_verdict == "DENIED":
            draft_verdict = "INCORRECT"
        else:
            draft_verdict = "PENDING"

        for pred_id in r.get("prediction_ids", []):
            # 이미 다른 검증 포인트에서 판정된 경우, CORRECT/INCORRECT 우선
            existing = drafts.get(pred_id)
            if existing and existing["draft_verdict"] in ("CORRECT", "INCORRECT"):
                continue

            drafts[pred_id] = {
                "id": pred_id,
                "draft_verdict": draft_verdict,
                "source_url": source_url,
                "fact": fact,
                "cp_id": cp_id,
            }

    # 저장
    draft_list = sorted(drafts.values(), key=lambda x: x["id"])
    out_path = OUTPUT_DIR / "stage4_drafts.json"
    out_path.write_text(
        json.dumps(draft_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 통계
    stats = {"CORRECT": 0, "INCORRECT": 0, "PENDING": 0}
    for d in draft_list:
        stats[d["draft_verdict"]] = stats.get(d["draft_verdict"], 0) + 1

    print(f"[Stage 4] 매핑 완료: {len(draft_list)}건")
    print(f"  CORRECT={stats['CORRECT']} INCORRECT={stats['INCORRECT']} PENDING={stats['PENDING']}")

    return drafts
