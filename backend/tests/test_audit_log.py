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


def test_log_event_swallows_missing_table_error_and_warns(audit_db, monkeypatch, tmp_path, caplog):
    """2026-09-29 CP-M2 교차 검토 지적: 예전엔 OperationalError만 잡고 완전히 조용히
    버렸다 - DB에 못 적어도 최소한 경고 로그는 남아야 나중에 "몇 건이 누락됐는지"를
    추적할 수 있다."""
    import logging
    monkeypatch.setattr(audit_log, "DB_PATH", str(tmp_path / "no_table.db"))
    with caplog.at_level(logging.WARNING):
        audit_db.log_event("llm_call", summary="테이블 없음")
    assert "감사 로그 기록 실패" in caplog.text


def test_list_events_filters_by_thread_id(audit_db):
    audit_db.log_event("llm_call", thread_id="t1", summary="a")
    audit_db.log_event("llm_call", thread_id="t2", summary="b")
    rows = audit_db.list_events(thread_id="t1")
    assert len(rows) == 1
    assert rows[0]["summary"] == "a"


def test_list_events_filters_by_time_range(audit_db):
    audit_db.log_event("llm_call", summary="이른 시각")
    early_ts = audit_db.list_events()[0]["ts"]
    audit_db.log_event("llm_call", summary="늦은 시각")

    rows = audit_db.list_events(since=early_ts)
    assert len(rows) == 2  # since는 이상(>=) 포함
    rows = audit_db.list_events(until="1999-01-01T00:00:00")
    assert len(rows) == 0


def test_list_events_caps_limit_to_max(audit_db, monkeypatch):
    monkeypatch.setattr(audit_log, "MAX_LIMIT", 3)
    for i in range(5):
        audit_db.log_event("llm_call", summary=str(i))
    assert len(audit_db.list_events(limit=1000)) == 3


def test_init_audit_table_creates_indexes(audit_db):
    """2026-09-29 CP-M2 교차 검토 지적: thread_id/event_type/ts로 자주 필터링하는데
    인덱스가 없었다 - 실제로 생성되는지 확인."""
    import sqlite3
    conn = sqlite3.connect(audit_log.DB_PATH)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_audit_log_thread_id" in names
    assert "idx_audit_log_event_type" in names
    assert "idx_audit_log_ts" in names
