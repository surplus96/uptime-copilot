"""
docs/ 폴더에 RAG용 서술형 매뉴얼을 실데이터 기반으로 생성한다.
COMPONENT_DESCRIPTIONS/ERROR_DESCRIPTIONS(교육용 추정 서술) + pump_manual.py(원심펌프 가정 표준 절차)
+ 실제 집계 통계를 결합해서 "증상 -> 점검 절차 -> 실제 통계" 문서를 만든다.
pdm_dataloader.py와 같은 성격의 1회 실행용 배치 스크립트.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # backend/ 를 import 경로에 추가

from pump_manual import (
    ANOMALY_PREWARNING_NOTE,
    ERROR5_NOTE,
    PUMP_MAINTENANCE_PROCEDURES,
    SIGNAL_TO_COMPONENT,
)

from data.pdm_operations import (
    COMPONENT_DESCRIPTIONS,
    ERROR_DESCRIPTIONS,
    get_component_failure_stats,
)

DB_PATH = Path(__file__).parent.parent / "store" / "pdm_telemetry.db"
DOCS_DIR = Path(__file__).parent / "docs"



def build_component_doc() -> str:
    """부품별 설명 + 표준 점검 절차 + 실제 이력 기준 가장 취약한 기종 통계."""
    lines = [
        "# 설비 부품별 정비 매뉴얼",
        "(데이터의 텔레메트리 신호 구성에 근거해 원심펌프로 가정한 가상 시나리오)\n",
    ]
    stats = get_component_failure_stats()
    for comp, desc in COMPONENT_DESCRIPTIONS.items():
        comp_stats = sorted(
            [s for s in stats if s["component"] == comp],
            key=lambda s: s["failures_per_machine"],
            reverse=True,
        )
        proc = PUMP_MAINTENANCE_PROCEDURES.get(comp, {})

        lines.append(f"## {comp}: {desc}")
        if proc.get("symptom"):
            lines.append(f"대표 증상: {proc['symptom']}")
        if proc.get("steps"):
            lines.append("표준 점검 절차:")
            for i, step in enumerate(proc["steps"], 1):
                lines.append(f"  {i}. {step}")
        if comp_stats:
            top = comp_stats[0]
            lines.append(
                f"실제 이력 기준: {top['model']} 기종에서 설비 1대당 평균 "
                f"{top['failures_per_machine']}회로 가장 잦게 발생함."
            )
        lines.append("")  # 부품 간 구분 빈 줄
    return "\n".join(lines)


def build_error_doc() -> str:
    """오류코드별 설명 + 전체 오류 이력 중 비중."""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]

    lines = ["# 오류 코드 안내\n"]
    for err, desc in ERROR_DESCRIPTIONS.items():
        count = conn.execute("SELECT COUNT(*) FROM errors WHERE errorID = ?", (err,)).fetchone()[0]
        pct = round(count / total * 100, 1) if total else 0
        lines.append(f"- {err}: {desc}. 전체 오류 이력 중 {pct}%를 차지함 (총 {count}건).")
    conn.close()

    lines.append(f"\n{ERROR5_NOTE}")
    return "\n".join(lines)

def build_anomaly_doc() -> str:
    """Z-score 기반 사전 경보 개념 자체를 설명하는 문서 - 이산적 오류코드와는 별개 개념임을 명시."""
    lines = ["# 사전 경보(텔레메트리 이상탐지) 안내\n", ANOMALY_PREWARNING_NOTE, ""]
    for signal, comp in SIGNAL_TO_COMPONENT.items():
        desc = COMPONENT_DESCRIPTIONS.get(comp, "")
        lines.append(f"- {signal} 신호 이상 -> {comp}({desc}) 관련 표준 절차 적용, 긴급도는 '주의'")
    return "\n".join(lines)



if __name__ == "__main__":
    DOCS_DIR.mkdir(exist_ok=True)
    (DOCS_DIR / "components.txt").write_text(build_component_doc(), encoding="utf-8")
    (DOCS_DIR / "errors.txt").write_text(build_error_doc(), encoding="utf-8")
    (DOCS_DIR / "anomaly_prewarning.txt").write_text(build_anomaly_doc(), encoding="utf-8")  # <- 추가
    print("생성된 파일:", [p.name for p in DOCS_DIR.glob("*.txt")])
