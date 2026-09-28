"""
MRO-M1 test-engineer 지적(2026-09-28): FR-01~06 신규 코드에 단위 테스트가 하나도
없어서 재고 부족 비교 반전 등 14개 변이가 전부 기존 테스트를 통과했다. check_parts()
는 실DB 조회 로직이라 mock 대신 임시 SQLite DB(parts_db fixture)로 격리해서 검증한다
- 공유 store/pdm_telemetry.db에 의존하면 CI/새 환경에서 재현 안 되는 문제가 반복된다
(2026-09-28 lifespan 테이블 미생성 사건과 같은 함정).
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import data.parts_operations as parts_ops

_COLUMNS = [
    "part_no", "component", "part_name", "lead_time_days", "on_hand", "min_stock",
    "unit_cost", "eol_date", "alternate_part_no", "source", "edition", "location", "verified_by",
]


@pytest.fixture
def parts_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_parts.db"
    monkeypatch.setattr(parts_ops, "DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE parts_master ({', '.join(_COLUMNS)})")
    conn.commit()
    conn.close()
    return db_path


def _insert(db_path, **overrides):
    row = {
        "part_no": "P1", "component": "comp1", "part_name": "테스트 부품", "lead_time_days": 10,
        "on_hand": 10, "min_stock": 5, "unit_cost": 100, "eol_date": None,
        "alternate_part_no": None, "source": "test", "edition": "v1", "location": "-", "verified_by": None,
    }
    row.update(overrides)
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"INSERT INTO parts_master VALUES ({','.join('?' * len(_COLUMNS))})",
        tuple(row[c] for c in _COLUMNS),
    )
    conn.commit()
    conn.close()


def test_no_shortage_when_on_hand_above_min(parts_db):
    _insert(parts_db, on_hand=10, min_stock=5)
    assert parts_ops.check_parts("comp1")["shortage"] is False


def test_shortage_boundary_equal_is_not_shortage(parts_db):
    """on_hand == min_stock은 부족이 아니다(< 비교, <= 아님) - 비교 연산자가 뒤집히는
    변이를 여기서 잡는다."""
    _insert(parts_db, on_hand=5, min_stock=5)
    assert parts_ops.check_parts("comp1")["shortage"] is False


def test_shortage_when_below_min(parts_db):
    _insert(parts_db, on_hand=4, min_stock=5)
    result = parts_ops.check_parts("comp1")
    assert result["shortage"] is True
    assert result["needs_procurement"] is True


def test_eol_imminent(parts_db):
    future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    _insert(parts_db, eol_date=future)
    result = parts_ops.check_parts("comp1")
    assert result["eol_soon"] is True
    assert result["eol_status"] == "임박"
    assert result["needs_procurement"] is True


def test_eol_passed_still_needs_procurement(parts_db):
    """2026-09-28 교차 검토 지적: 단종일이 지나도 영원히 '임박'으로 남던 버그. 지난
    날짜는 '경과'로 구분되지만 조달 필요성은 여전히 True여야 한다(더 급한 상태지
    해소된 게 아니므로)."""
    past = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    _insert(parts_db, eol_date=past)
    result = parts_ops.check_parts("comp1")
    assert result["eol_status"] == "경과"
    assert result["needs_procurement"] is True


def test_no_eol_when_date_is_null(parts_db):
    _insert(parts_db, eol_date=None)
    result = parts_ops.check_parts("comp1")
    assert result["eol_soon"] is False
    assert result["eol_status"] is None


def test_alternate_info_reports_its_own_shortage(parts_db):
    """2026-09-28 교차 검토 지적: 대체품 자체의 재고를 확인 안 해서 '검토 필요'가
    해결책처럼 읽혔던 문제. 대체품도 부족하면 그 사실이 그대로 나와야 한다."""
    future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    _insert(parts_db, part_no="P1", eol_date=future, alternate_part_no="P2")
    _insert(parts_db, part_no="P2", on_hand=1, min_stock=5)
    result = parts_ops.check_parts("comp1")
    assert result["alternate_info"]["part_no"] == "P2"
    assert result["alternate_info"]["shortage"] is True


def test_alternate_info_reports_healthy_stock(parts_db):
    future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    _insert(parts_db, part_no="P1", eol_date=future, alternate_part_no="P2")
    _insert(parts_db, part_no="P2", on_hand=50, min_stock=5)
    result = parts_ops.check_parts("comp1")
    assert result["alternate_info"]["shortage"] is False


def test_alternate_fields_hidden_when_not_eol(parts_db):
    """eol_soon이 False면 alternate_part_no/alternate_info도 노출 안 한다 - 단종
    안 된 부품에 대체품 정보가 뜨면 혼란을 준다."""
    _insert(parts_db, on_hand=10, min_stock=5, eol_date=None, alternate_part_no="P2")
    result = parts_ops.check_parts("comp1")
    assert result["alternate_part_no"] is None
    assert result["alternate_info"] is None


def test_missing_table_returns_error_not_exception(tmp_path, monkeypatch):
    """2026-09-28 교차 검토 지적: 테이블 없는 새 환경(CI, 첫 배포)에서 500 에러로
    죽던 문제. 예외 대신 {"error": ...}를 반환해야 한다."""
    monkeypatch.setattr(parts_ops, "DB_PATH", str(tmp_path / "empty.db"))
    result = parts_ops.check_parts("comp1")
    assert "error" in result


def test_unknown_component_returns_error(parts_db):
    _insert(parts_db, component="comp1")
    result = parts_ops.check_parts("comp99")
    assert "error" in result
