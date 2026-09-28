"""
MRO-FR-01: 합성 부품 마스터 생성. docs/design/parts_assumptions.md의 가정을 그대로
코드화한다 - 모든 값은 합성값이다(H3 규칙, parts_assumptions.md 참고).
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")

# 명시적으로 dict[str, Any]로 선언 - 안 그러면 mypy가 값 타입이 섞인 리터럴에서
# dict[str, object]를 추론해서, 이후 p["component"]로 str 딕셔너리를 인덱싱하거나
# p["part_no"].endswith(...)를 호출하는 곳에서 타입 오류를 낸다(2026-09-28 교차
# 검토 지적, mypy로 직접 재현 확인).
BASE_PARTS: list[dict[str, Any]] = [
    {"part_no": "SYN-C1-001", "component": "comp1", "part_name": "전동기 권선 어셈블리", "lead_time_days": 14},
    {"part_no": "SYN-C2-001", "component": "comp2", "part_name": "회전자·베어링 유닛", "lead_time_days": 21},
    {"part_no": "SYN-C3-001", "component": "comp3", "part_name": "기계식 씰 키트", "lead_time_days": 10},
    {"part_no": "SYN-C4-001", "component": "comp4", "part_name": "진동 저감 댐퍼 (구형)", "lead_time_days": 45},
    {"part_no": "SYN-C4-002", "component": "comp4", "part_name": "진동 저감 댐퍼 (신형, 호환)", "lead_time_days": 60},
]


def init_parts_table() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parts_master (
            part_no TEXT PRIMARY KEY,
            component TEXT,
            part_name TEXT,
            lead_time_days INTEGER,
            on_hand INTEGER,
            min_stock INTEGER,
            unit_cost REAL,
            eol_date TEXT,
            alternate_part_no TEXT,
            source TEXT,
            edition TEXT,
            location TEXT,
            verified_by TEXT
        )
    """)
    conn.commit()
    conn.close()


def generate_parts_master() -> int:
    """comp4(SYN-C4-001)만 재고를 낮게, 단종 임박으로 설정한다 - MRO-FR-05(재고 부족
    + 고위험 -> 조달 권고) 시나리오가 실제로 트리거되도록 하기 위한 의도적 설계다
    (parts_assumptions.md §4). 재고는 시드를 고정해서 재실행해도 항상 같은 값이 나온다.

    2026-09-28 CP-M1 사전 수정: comp1~3의 on_hand를 애초에 5~20으로 잡았던 건(P-2,
    9/27) FR-02가 나중에 계산한 "설비 100대 전체 30일 수요"(comp당 57~63개)를 몰랐던
    채 정한 값이었다 - 그 결과 comp1~3도 comp4처럼 "심각한 재고 부족"으로 보여서
    comp4만 특별히 문제라는 시연 의도가 흐려졌다(FR-06 대시보드에서 실측으로 발견).
    정상 부품은 30일 수요보다 넉넉히 위(70~100)로 올려서 comp4와의 대비를 살린다."""
    import random
    random.seed(42)

    for p in BASE_PARTS:
        if p["component"] == "comp4":
            # 0~2로 좁혀서 min_stock=3보다 항상 작게 만든다 - 원래 randint(0,5)는
            # 절반의 확률로 shortage=False가 나올 수 있었는데(재고부족이 "우연히"
            # 걸린 것), 다른 부품의 난수 범위를 바꾸면 이 우연성이 그대로 깨진다는 걸
            # 실측으로 확인했다(2026-09-28) - 데모 시나리오는 RNG 순서 변경에
            # 취약하지 않게 확정적으로 보장해야 한다.
            p["on_hand"] = random.randint(0, 2)
            p["min_stock"] = 3
            p["unit_cost"] = 250
        else:
            p["on_hand"] = random.randint(70, 100)
            p["min_stock"] = 20
            p["unit_cost"] = {"comp1": 100, "comp2": 120, "comp3": 80}[p["component"]]

        if p["part_no"] == "SYN-C4-001":
            p["eol_date"] = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
            p["alternate_part_no"] = "SYN-C4-002"
        else:
            p["eol_date"] = None
            p["alternate_part_no"] = None

        p["source"] = "docs/design/parts_assumptions.md"
        p["edition"] = "synthetic-v1"
        p["location"] = f"§2-{p['component']}-{'대체' if p['part_no'].endswith('002') else '주'}"
        p["verified_by"] = None  # H3 규칙: 사용자만 채운다

    conn = sqlite3.connect(DB_PATH)
    for p in BASE_PARTS:
        conn.execute(
            """INSERT OR REPLACE INTO parts_master
               (part_no, component, part_name, lead_time_days, on_hand, min_stock,
                unit_cost, eol_date, alternate_part_no, source, edition, location, verified_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["part_no"], p["component"], p["part_name"], p["lead_time_days"],
             p["on_hand"], p["min_stock"], p["unit_cost"], p["eol_date"],
             p["alternate_part_no"], p["source"], p["edition"], p["location"], p["verified_by"]),
        )
    conn.commit()
    conn.close()

    print(f"부품 마스터 {len(BASE_PARTS)}건 적재 완료 -> {DB_PATH}")
    return len(BASE_PARTS)


if __name__ == "__main__":
    init_parts_table()
    generate_parts_master()
