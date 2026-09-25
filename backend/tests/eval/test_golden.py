import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agent.agent_service as agent_service
from agent.agent_service import SupervisorState

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"

UNGROUNDED_CATEGORY = "일반_문의"  # DB 조회 없는 자유생성으로 빠지는 유일한 카테고리
MACHINE_CATEGORIES = ("오류_진단", "정비_일정")


def _load_golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.mark.eval
def test_golden_set_accuracy():
    """실제 route_node()를 그대로 호출한다 - 프롬프트를 여기서 따로 복제하지 않는다.
    예전엔 이 테스트가 route_node의 안전장치(정규식 오분류 방지)를 건너뛴 채 프롬프트만
    복제해서 돌렸는데, 그러면 실제로 배포되는 동작과 이 숫자가 어긋날 수 있었다
    (2026-09-25, docs/decisions.md 라우터 버그 발견 이후 반영)."""
    cases = _load_golden()
    router_correct = 0
    extraction_correct = 0
    reask_correct = 0
    reask_total = 0
    misroute_to_ungrounded = []
    dangerous_misroutes = []
    rows = []

    for case in cases:
        state = SupervisorState(user_message=case["user_message"])
        route_result = agent_service.route_node(state)
        predicted_category = route_result["category"]
        predicted_machine_id = route_result["machine_id"]

        router_ok = predicted_category == case["expected_category"]
        router_correct += router_ok

        # 오분류의 도착지: 실제 설비 관련 문의(오류_진단/정비_일정)인데 근거 없는
        # general_node(일반_문의)로 빠지는 것 자체가 위험한 게 아니라, "실존하는 특정
        # 설비"에 대해 근거 없이 자신감 있는 답을 낼 수 있다는 게 위험하다(2026-09-25
        # 실측: 46번 설비 사례). expected_machine_id가 있는 케이스가 여기 해당하며
        # 무관용으로 본다. machine_id가 없는 케이스(예: "설비가 이상해요")는 같은 경로로
        # 빠져도 특정 설비에 대해 지어낼 대상 자체가 없어 심각도가 다르고, 이 문형은 모델
        # 판정이 실행마다 흔들리는 것도 실측으로 확인됨(docs/decisions.md) - 무관용
        # 어서션 대신 지표로만 남긴다.
        misroute_is_ungrounded = (
            not router_ok
            and case["expected_category"] in MACHINE_CATEGORIES
            and predicted_category == UNGROUNDED_CATEGORY
        )
        misroute_is_dangerous = misroute_is_ungrounded and case["expected_machine_id"] is not None
        if misroute_is_ungrounded:
            misroute_to_ungrounded.append(case["id"])
        if misroute_is_dangerous:
            dangerous_misroutes.append(case["id"])

        extraction_ok = True
        if case["expected_category"] in MACHINE_CATEGORIES:
            extraction_ok = predicted_machine_id == case["expected_machine_id"]
            extraction_correct += extraction_ok
            if case["expected_machine_id"] is None:
                reask_total += 1
                reask_correct += predicted_machine_id is None

        rows.append({
            "id": case["id"], "user_message": case["user_message"],
            "expected_category": case["expected_category"], "predicted_category": predicted_category,
            "router_ok": router_ok, "misroute_to_ungrounded": misroute_is_ungrounded,
            "misroute_is_dangerous": misroute_is_dangerous,
            "expected_machine_id": case["expected_machine_id"], "predicted_machine_id": predicted_machine_id,
            "extraction_ok": extraction_ok,
        })

    n = len(cases)
    n_extractable = sum(1 for c in cases if c["expected_category"] in MACHINE_CATEGORIES)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": n,
        "router_accuracy": round(router_correct / n, 3),
        "extraction_accuracy": round(extraction_correct / n_extractable, 3) if n_extractable else None,
        "reask_accuracy": round(reask_correct / reask_total, 3) if reask_total else None,
        "misroute_to_ungrounded_ids": misroute_to_ungrounded,
        "dangerous_misroute_ids": dangerous_misroutes,
        "rows": rows,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{summary['run_at'].replace(':', '-')}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n라우터 정확도: {summary['router_accuracy']:.1%}, 추출 정확도: {summary['extraction_accuracy']:.1%}, "
          f"되묻기 정확도: {summary['reask_accuracy']:.1%}, 근거없는 노드로 오분류: "
          f"{len(misroute_to_ungrounded)}건(그 중 특정 설비 번호 있음 {len(dangerous_misroutes)}건) -> {out_path}")

    assert summary["router_accuracy"] >= 0.7, f"라우터 정확도가 기준(70%) 미달: {summary['router_accuracy']:.1%}"
    # 무관용 회귀 테스트: 실존하는 설비 번호가 있는 문의가 근거 없는 노드로 빠지는 것만
    # 막는다(2026-09-25 실측한 46번 설비 사례가 정확히 이 조건). 번호 없는 misroute는
    # 위 print/JSON에 지표로만 남기고 어서션에서는 뺀다 - 이 문형은 모델 판정 자체가
    # 실행마다 흔들려서(docs/decisions.md) 무관용으로 걸면 코드 변경 없이도 실행마다
    # 테스트가 빨간불/초록불을 오가는 flaky 테스트가 된다.
    assert not dangerous_misroutes, (
        f"실존 설비 번호가 있는 문의가 근거 없는 노드로 빠짐(안전장치 사각지대): {dangerous_misroutes}"
    )


@pytest.mark.eval
def test_perspective_assessment_consistency():
    """안전/생산/정비 관점 평가는 골든셋(라우팅/추출만 다룸) 범위 밖이라 지금까지 실제
    문제(risk_level='중간'인데 requires_shutdown=true인 모순, 2026-09-25 실측)가 스팟
    체크로만 우연히 발견됐다. 정식 평가 항목으로 편입한다.

    risk_level에 대한 '정답'은 도메인 판단이 필요해 라벨링돼 있지 않으므로 의미 품질을
    채점하진 않는다 - 대신 (1) 구조화 출력이 예외 없이 파싱되는지, (2) requires_shutdown이
    risk_level/recommended_window로부터 항상 일관되게 유도되는지(모순 불가능성 자체를
    검증)를 확인하는 커버리지 스모크 테스트다."""
    diagnoses = [
        "최근 오류 이력: 2027-08-01 error4(진동 이상 경고) / 실제 고장 발생: 2027-08-02 진동 저감 장치",
        "최근 오류 이력: 2027-08-01 error2(회전속도 이상 경고) / 텔레메트리 이상 감지(사전 경보): rotate 신호가 평소 대비 벗어남",
        "최근 오류 이력 없음",
    ]
    prompts = {
        "안전": "당신은 현장 안전 담당자입니다. 아래 사고 상황의 안전 위험도를 평가하세요.",
        "생산": "당신은 생산 관리자입니다. 아래 사고 상황이 생산에 미치는 영향을 평가하세요.",
        "정비": "당신은 정비 기술자입니다. 아래 사고 상황의 수리 난이도를 평가하세요.",
    }

    rows = []
    for diagnosis in diagnoses:
        for label, prompt in prompts.items():
            result = agent_service._assess_perspective(label, prompt, diagnosis)
            assessment = result["perspective_assessments"][0]
            expected_shutdown = agent_service._derive_requires_shutdown(
                assessment["risk_level"], assessment["recommended_window"]
            )
            rows.append({
                "diagnosis": diagnosis, "label": label,
                "risk_level": assessment["risk_level"],
                "recommended_window": assessment["recommended_window"],
                "requires_shutdown": assessment["requires_shutdown"],
                "is_fallback": assessment["is_fallback"],
            })
            assert assessment["requires_shutdown"] == expected_shutdown, (
                f"{label} 관점 requires_shutdown이 risk_level/recommended_window와 불일치: {assessment}"
            )

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"perspective-{datetime.now(timezone.utc).isoformat().replace(':', '-')}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n관점 평가 {len(rows)}건 일관성 확인 완료 -> {out_path}")
