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


def test_log_event_masks_pii_in_summary(audit_db):
    """감사 로그는 인증 없는 조회 API로 노출되고 보존 기한도 없다 - 사용자 원문(RAG 질문
    등)에 섞인 PII를 그대로 영구 저장하면 안 된다(security-reviewer 인계, 2026-09-29)."""
    audit_db.log_event(
        "llm_call", target="rag_query",
        summary="내 번호는 010-1234-5678이고 주민번호 900101-1234567 입니다",
    )
    row = audit_db.list_events()[0]
    assert "010-1234-5678" not in row["summary"]
    assert "900101-1234567" not in row["summary"]
    assert "[전화번호 마스킹]" in row["summary"]
    assert "[주민등록번호 마스킹]" in row["summary"]


def test_log_event_leaves_non_pii_summary_untouched(audit_db):
    audit_db.log_event("llm_call", target="rag_query", summary="comp3 부품은 어떤 증상과 관련있어?")
    assert audit_db.list_events()[0]["summary"] == "comp3 부품은 어떤 증상과 관련있어?"


def test_pii_straddling_the_truncation_boundary_is_masked(audit_db):
    """2026-09-30 code-quality/security-reviewer(실행으로 확인): 호출부가 먼저 50자로 자르고
    마스킹하면 경계에 걸친 주민번호가 '900101-' 같은 조각으로 저장돼 패턴을 통과했다. 이제
    마스킹을 먼저 하고 자른다."""
    audit_db.log_event("llm_call", target="rag_query", summary="가" * 44 + " 900101-1234567 입니다")
    stored = audit_db.list_events(limit=1)[0]["summary"]
    assert "900101" not in stored
    assert "[주민등록번호 마스킹]" in stored


@pytest.mark.parametrize("text, label", [
    ("문의는 kim.taeyoung+work@example.co.kr 로", "이메일"),
    ("연락처 +82 10-1234-5678", "국제전화번호"),
    ("사무실 02-345-6789", "유선전화번호"),
])
def test_audit_only_pii_patterns_are_masked(audit_db, text, label):
    audit_db.log_event("llm_call", summary=text)
    stored = audit_db.list_events(limit=1)[0]["summary"]
    assert f"[{label} 마스킹]" in stored
    for raw in ("kim.taeyoung", "1234-5678", "345-6789"):
        assert raw not in stored


@pytest.mark.parametrize("text", [
    "comp3 부품은 어떤 증상과 관련있어?",
    "2015-01-01 06:00 설비 #12 comp4 오류",   # 타임스탬프·설비번호를 전화번호로 오탐하면 안 된다
])
def test_non_pii_summary_is_left_untouched(audit_db, text):
    audit_db.log_event("llm_call", summary=text)
    assert audit_db.list_events(limit=1)[0]["summary"] == text


def test_long_summary_is_truncated_after_masking(audit_db):
    audit_db.log_event("llm_call", summary="가" * 300)
    stored = audit_db.list_events(limit=1)[0]["summary"]
    assert len(stored) == audit_db.MAX_SUMMARY_LENGTH + 1 and stored.endswith("…")


def test_log_event_never_raises_even_when_the_db_is_unusable(tmp_path, monkeypatch, caplog):
    """감사 기록 실패가 업무 흐름(승인·알림)을 막으면 안 된다는 계약 - 호출부 여러 곳이 이
    전제에 기대고 있는데 지금까지 어떤 테스트도 못박지 않았다(2026-09-30 code-quality-reviewer).
    OperationalError뿐 아니라 DatabaseError(손상된 파일)·디렉터리 경로도 삼키고 경고를 남긴다."""
    from data import audit_log

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("이건 SQLite 파일이 아니다")
    for bad_path in (str(corrupt), str(tmp_path), str(tmp_path / "no" / "such" / "dir" / "x.db")):
        monkeypatch.setattr(audit_log, "DB_PATH", bad_path)
        with caplog.at_level("WARNING"):
            audit_log.log_event("approval", thread_id="t", target="설비#1", summary="승인")  # 예외 없어야 함
    assert caplog.text.count("감사 로그 기록 실패") == 3


def test_email_masking_runs_in_linear_time_on_hostile_input(audit_db):
    """2026-09-30 code-quality-reviewer: 예전 이메일 정규식은 이차 시간이라 1만 자에 1.5초, GIL을 잡아
    이벤트 루프까지 멈췄다(지금은 입력이 2000자로 제한돼 있어 잠재적). 선형이면 10만 자도 밀리초다."""
    import time

    # 2만 자면 이차 정규식은 수 초(실측 6.5초)가 걸리고 선형이면 밀리초다. 더 길게 잡으면 회귀했을 때
    # 이 테스트 자체가 몇 분씩 걸려서(10만 자에서 156초 실측) 실패를 알리는 데 너무 오래 걸린다.
    hostile = "a" * 20_000 + "@" + "b-" * 10_000
    started = time.perf_counter()
    audit_db._mask_pii(hostile)
    assert time.perf_counter() - started < 1.0


def test_target_field_is_masked_too(audit_db):
    """summary만 마스킹하고 target을 빠뜨려도 통과했다(2026-09-30 test-engineer, AL11)."""
    audit_db.log_event("x", target="kim@example.com")
    stored = audit_db.list_events(limit=1)[0]["target"]
    assert "[이메일 마스킹]" in stored and "kim@" not in stored


def test_masking_exceptions_never_escape_log_event(audit_db, monkeypatch, caplog):
    """마스킹이 try 밖에서 예외를 던지면 업무 흐름까지 번진다 - log_event는 어떤 경우에도 던지지 않는다."""
    def boom(text):
        raise RuntimeError("마스킹 실패")

    monkeypatch.setattr(audit_db, "_mask_pii", boom)
    with caplog.at_level("WARNING"):
        audit_db.log_event("x", summary="아무거나")   # 예외 없어야 함
    assert "감사 로그 기록 실패" in caplog.text


@pytest.mark.parametrize("text", ["부품번호 0123456789", "WO-012-3456-7890X", "SYN-C4-001 재고 0212345678"])
def test_digit_runs_that_look_like_ids_are_not_treated_as_landline_numbers(audit_db, text):
    audit_db.log_event("x", summary=text)
    assert audit_db.list_events(limit=1)[0]["summary"] == text


def test_summary_truncation_length_is_pinned_independently_of_the_constant(audit_db):
    """상한 값을 상수에서 읽어 비교하면 상수를 바꿔도 통과한다 - 100자로 못박는다."""
    audit_db.log_event("x", summary="가" * 500)
    assert len(audit_db.list_events(limit=1)[0]["summary"]) == 101
