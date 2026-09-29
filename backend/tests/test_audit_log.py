"""MRO-FR-08: audit_log.py 격리 테스트 - test_parts_operations.py와 같은 패턴,
공유 DB(store/pdm_telemetry.db)에 의존하면 CI/새 환경에서 재현 안 되는 함정을
피하기 위해 monkeypatch로 DB_PATH를 tmp_path로 교체한다."""
import pytest

from data import audit_log


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_audit.db")
    monkeypatch.setattr(audit_log, "DB_PATH", db_path)
    audit_log.init_audit_table()
    return audit_log


def test_log_event_defaults_result_to_success(audit_db):
    audit_db.log_event("llm_call", thread_id="t1", target="안전", summary="테스트")
    rows = audit_db.list_events()
    assert rows[0]["result"] == "성공"


def test_log_event_records_all_fields(audit_db):
    audit_db.log_event(
        "approval", thread_id="t1", target="설비#1", summary="긴급 승인",
        result="성공", provider="ollama",
    )
    row = audit_db.list_events()[0]
    assert row["thread_id"] == "t1"
    assert row["event_type"] == "approval"
    assert row["target"] == "설비#1"
    assert row["provider"] == "ollama"


def test_list_events_orders_newest_first(audit_db):
    audit_db.log_event("llm_call", summary="첫번째")
    audit_db.log_event("llm_call", summary="두번째")
    rows = audit_db.list_events()
    assert rows[0]["summary"] == "두번째"
    assert rows[1]["summary"] == "첫번째"


def test_list_events_filters_by_event_type(audit_db):
    audit_db.log_event("llm_call", summary="a")
    audit_db.log_event("approval", summary="b")
    rows = audit_db.list_events(event_type="approval")
    assert len(rows) == 1
    assert rows[0]["summary"] == "b"


def test_list_events_respects_limit(audit_db):
    for i in range(5):
        audit_db.log_event("llm_call", summary=str(i))
    assert len(audit_db.list_events(limit=2)) == 2


def test_log_event_swallows_missing_table_error(audit_db, monkeypatch, tmp_path):
    """audit_log 기록 실패가 본 기능(진단/승인/알림)을 막으면 안 된다 - 테이블이
    없는 상태(예: init 실행 전 레이스)에서도 예외를 던지지 않아야 한다."""
    monkeypatch.setattr(audit_log, "DB_PATH", str(tmp_path / "no_table.db"))
    audit_db.log_event("llm_call", summary="테이블 없음")  # 예외 없이 조용히 무시돼야 함
