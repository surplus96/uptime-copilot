import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agent.agent_service as agent_service

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


def _load_golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.mark.eval
def test_golden_set_accuracy():
    cases = _load_golden()
    router_correct = 0
    extraction_correct = 0
    reask_correct = 0
    reask_total = 0
    rows = []

    for case in cases:
        route_completion = agent_service.client.chat.completions.parse(
            model=agent_service.MODEL,
            reasoning_effort="none",
            messages=[
                {"role": "system", "content": "사용자 문의를 오류_진단/정비_일정/일반_문의 중 하나로 분류하세요."},
                {"role": "user", "content": case["user_message"]},
            ],
            response_format=agent_service.RouteDecision,
        )
        predicted_category = route_completion.choices[0].message.parsed.category
        router_ok = predicted_category == case["expected_category"]
        router_correct += router_ok

        predicted_machine_id = None
        extraction_ok = True
        if case["expected_category"] in ("오류_진단", "정비_일정"):
            predicted_machine_id = agent_service._extract_machine_id(case["user_message"])
            extraction_ok = predicted_machine_id == case["expected_machine_id"]
            extraction_correct += extraction_ok
            if case["expected_machine_id"] is None:
                reask_total += 1
                reask_correct += predicted_machine_id is None

        rows.append({
            "id": case["id"], "user_message": case["user_message"],
            "expected_category": case["expected_category"], "predicted_category": predicted_category,
            "router_ok": router_ok,
            "expected_machine_id": case["expected_machine_id"], "predicted_machine_id": predicted_machine_id,
            "extraction_ok": extraction_ok,
        })

    n = len(cases)
    n_extractable = sum(1 for c in cases if c["expected_category"] in ("오류_진단", "정비_일정"))
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": n,
        "router_accuracy": round(router_correct / n, 3),
        "extraction_accuracy": round(extraction_correct / n_extractable, 3) if n_extractable else None,
        "reask_accuracy": round(reask_correct / reask_total, 3) if reask_total else None,
        "rows": rows,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{summary['run_at'].replace(':', '-')}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n라우터 정확도: {summary['router_accuracy']:.1%}, 추출 정확도: {summary['extraction_accuracy']:.1%}, "
          f"되묻기 정확도: {summary['reask_accuracy']:.1%} -> {out_path}")

    assert summary["router_accuracy"] >= 0.7, f"라우터 정확도가 기준(70%) 미달: {summary['router_accuracy']:.1%}"
