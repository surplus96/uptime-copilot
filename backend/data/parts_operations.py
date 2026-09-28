import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


def check_parts(component: str) -> dict:
    """부품 가용성 조회 - 순수 함수(DB 읽기만, 부작용 없음). agent_service.py의
    parts_check 노드와 scan_machines(그래프 밖) 양쪽이 이 함수 하나를 공유한다 -
    둘 중 하나에만 로직을 넣으면 다른 경로가 놓친다(2026-09-27 교차 검토 지적,
    scan_machines가 그래프를 완전히 우회하는 걸 확인했음)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM parts_master WHERE component = ?", (component,)
        ).fetchall()]
    except sqlite3.OperationalError as e:
        # 테이블이 아직 없는 상태(예: lifespan 초기화 전, 또는 init 실패) - 500 에러로
        # 앱 전체를 죽이는 대신 get_machine_info()와 같은 {"error": ...} 관례로 응답한다
        # (2026-09-28 교차 검토 지적: 새 볼륨에서 이 경로가 실제로 죽는 걸 확인함).
        conn.close()
        return {"component": component, "error": f"부품 마스터 조회 실패: {e}"}
    conn.close()

    if not rows:
        return {"component": component, "error": f"부품 마스터에 {component} 없음"}

    # 주부품 = 다른 행의 alternate_part_no로 지목되지 않은 쪽
    alternate_ids = {r["alternate_part_no"] for r in rows if r["alternate_part_no"]}
    primary = next((r for r in rows if r["part_no"] not in alternate_ids), rows[0])

    shortage = primary["on_hand"] < primary["min_stock"]

    # 2026-09-28 교차 검토 지적: eol_date 존재 여부만 보면 단종일이 지나도 영원히
    # "임박"으로 표시된다. 실제 날짜와 비교해서 "임박"/"경과"를 구분한다. 부품 도메인은
    # 실제 조달에 걸리는 달력 일수를 다루므로 앱의 시뮬레이터 시계가 아니라 실제
    # datetime.now()를 기준으로 삼는다(eol_date 자체도 생성 시 datetime.now() 기준).
    eol_status = None
    if primary["eol_date"] is not None:
        eol_dt = datetime.strptime(primary["eol_date"], "%Y-%m-%d")
        eol_status = "임박" if eol_dt >= datetime.now() else "경과"
    eol_soon = eol_status is not None  # 하위 호환 - "임박"·"경과" 둘 다 True(둘 다 조달 필요)

    # 2026-09-28 교차 검토 지적: 대체품을 제안만 하고 그 부품 자체의 재고는 확인 안
    # 했었다 - SYN-C4-002(대체품)도 실제로는 주부품과 똑같이 재고 부족 상태였음.
    # "검토 필요"가 해결책처럼 읽히지 않도록, 대체품 자체의 가용성도 같이 보고한다.
    alternate_info = None
    if primary["alternate_part_no"]:
        alt_row = next((r for r in rows if r["part_no"] == primary["alternate_part_no"]), None)
        if alt_row:
            alternate_info = {
                "part_no": alt_row["part_no"],
                "on_hand": alt_row["on_hand"],
                "min_stock": alt_row["min_stock"],
                "lead_time_days": alt_row["lead_time_days"],
                "shortage": alt_row["on_hand"] < alt_row["min_stock"],
            }

    return {
        "component": component,
        "part_no": primary["part_no"],
        "part_name": primary["part_name"],
        "on_hand": primary["on_hand"],
        "min_stock": primary["min_stock"],
        "lead_time_days": primary["lead_time_days"],
        "shortage": shortage,
        "eol_soon": eol_soon,
        "eol_status": eol_status,
        "eol_date": primary["eol_date"],
        "alternate_part_no": primary["alternate_part_no"] if eol_soon else None,
        "alternate_info": alternate_info if eol_soon else None,
        "needs_procurement": shortage or eol_soon,
    }


if __name__ == "__main__":
    print("comp4(재고 부족+단종 예상):", check_parts("comp4"))
    print("comp1(정상 부품 예상):", check_parts("comp1"))
