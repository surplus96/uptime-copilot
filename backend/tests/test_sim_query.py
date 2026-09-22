"""sim_query.py 회귀 테스트 - 원본 데이터와 시뮬레이션 데이터를 합쳐 조회하는 계층.

2026-09-22 실제 사고 근거: sim_query.sim_only_now()가 손으로 파일을 고치는 도중
조용히 통째로 사라졌는데, 테스트가 하나도 없어서 아무도 못 잡았다. 15초마다 도는
sim_loop._tick_once()는 bare except로 AttributeError를 70분 동안 삼켰고,
GET /simulator/status가 브라우저에서 500을 낸 뒤에야 드러났다.
그래서 이 파일의 첫 번째 테스트는 "호출자가 쓰는 이름이 실제로 존재하는가"다.

각 테스트는 대응하는 규칙을 코드에서 지웠을 때 실패하는 것까지 직접 검증했다
(변이 테스트 - test_sim_engine.py와 같은 방식).

실제 pdm_telemetry.db는 절대 건드리지 않는다: conftest.py의 event_store_module
패턴대로 DB_PATH를 tmp_path 임시 파일로 바꿔치기한다. sim_* 스키마는 직접 손으로
쓰지 않고 운영 코드인 sim_store.init_sim_tables()를 그대로 호출해서 만든다
(손으로 베낀 스키마가 운영 스키마와 갈라지는 걸 막기 위함).
"""
import ast
import sqlite3
from pathlib import Path

import pytest

from data import sim_query, sim_store

BACKEND_DIR = Path(__file__).parent.parent

# 운영 DB(backend/store/pdm_telemetry.db)에서 그대로 떠온 원본 테이블 DDL.
# sim_query가 원본 테이블을 조회할 때 쓰는 따옴표 붙은 "machineID" 컬럼명까지 동일해야 한다.
ORIGINAL_DDL = [
    'CREATE TABLE "telemetry" ("datetime" TIMESTAMP, "machineID" INTEGER, '
    '"volt" REAL, "rotate" REAL, "pressure" REAL, "vibration" REAL)',
    'CREATE TABLE "errors" ("datetime" TIMESTAMP, "machineID" INTEGER, "errorID" TEXT)',
    'CREATE TABLE "failures" ("datetime" TIMESTAMP, "machineID" INTEGER, "failure" TEXT)',
    'CREATE TABLE "maint" ("datetime" TIMESTAMP, "machineID" INTEGER, "comp" TEXT)',
]


class _DB:
    """테스트가 행을 넣을 때 쓰는 얇은 헬퍼."""

    def __init__(self, path: str):
        self.path = path

    def insert(self, table: str, rows: list[tuple]) -> None:
        conn = sqlite3.connect(self.path)
        placeholders = ", ".join("?" * len(rows[0]))
        conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.commit()
        conn.close()


@pytest.fixture
def orig_only_db(tmp_path, monkeypatch):
    """시뮬레이터를 한 번도 켠 적 없는 상태 - sim_* 테이블이 아예 없다."""
    path = str(tmp_path / "sim_query_test.db")
    monkeypatch.setattr(sim_query, "DB_PATH", path)
    monkeypatch.setattr(sim_store, "DB_PATH", path)
    conn = sqlite3.connect(path)
    for ddl in ORIGINAL_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return _DB(path)


@pytest.fixture
def sim_db(orig_only_db):
    """시뮬레이터를 켠 상태 - 운영 코드로 sim_* 테이블을 만든다(아직 행은 없음)."""
    sim_store.init_sim_tables()
    return orig_only_db


# --------------------------------------------------------------------------
# 0. 실제로 터진 사고: 호출자가 쓰는 함수가 모듈에서 사라졌다
# --------------------------------------------------------------------------

def test_every_name_callers_use_still_exists_on_sim_query():
    """backend/ 안에서 `sim_query.X`로 부르는 이름이 전부 실제로 존재해야 한다.

    2026-09-22 사고 재현 방지용. sim_only_now()가 사라졌을 때 이 테스트가 있었다면
    import 시점이 아니라 여기서 바로 빨간 불이 켜졌다. 호출 목록을 손으로 적지 않고
    AST로 긁어오기 때문에, 나중에 호출자가 늘어도 자동으로 따라온다.
    """
    used: dict[str, str] = {}
    for py in BACKEND_DIR.rglob("*.py"):
        if ".venv" in py.parts or "tests" in py.parts or py.name == "sim_query.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "sim_query"
            ):
                used.setdefault(node.attr, f"{py.relative_to(BACKEND_DIR)}:{node.lineno}")

    assert used, "sim_query 호출부를 하나도 못 찾았다 - 이 테스트 자체가 고장난 것"
    missing = {name: where for name, where in used.items() if not callable(getattr(sim_query, name, None))}
    assert not missing, f"sim_query에서 사라진 함수: {missing}"


# --------------------------------------------------------------------------
# 1. recent_rows - 원본 + 시뮬레이션 병합
# --------------------------------------------------------------------------

def test_recent_rows_merges_both_sources(sim_db):
    sim_db.insert("errors", [("2016-01-01 06:00:00", 1, "error1")])
    sim_db.insert("sim_errors", [("2016-01-02 06:00:00", 1, "error2")])

    rows = sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], 1, 10)

    assert rows == [("2016-01-01 06:00:00", "error1"), ("2016-01-02 06:00:00", "error2")]


def test_recent_rows_interleaves_sources_chronologically(sim_db):
    """시뮬레이션 행이 원본 행 '사이'에 끼는 경우. 병합 후 정렬을 안 하고 그냥 이어붙이면
    원본 블록 뒤에 시뮬 블록이 통째로 붙어서 이 순서가 깨진다."""
    sim_db.insert("errors", [("2016-01-01 06:00:00", 1, "orig-a"), ("2016-01-03 06:00:00", 1, "orig-b")])
    sim_db.insert("sim_errors", [("2016-01-02 06:00:00", 1, "sim-a"), ("2016-01-04 06:00:00", 1, "sim-b")])

    rows = sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], 1, 10)

    assert [r[1] for r in rows] == ["orig-a", "sim-a", "orig-b", "sim-b"]


def test_recent_rows_keeps_the_newest_rows_when_over_limit(sim_db):
    """limit을 넘으면 '가장 오래된 N개'가 아니라 '가장 최신 N개'가 남아야 한다."""
    sim_db.insert("errors", [(f"2016-01-0{d} 06:00:00", 1, f"orig-{d}") for d in range(1, 5)])
    sim_db.insert("sim_errors", [(f"2016-01-0{d} 12:00:00", 1, f"sim-{d}") for d in range(5, 9)])

    rows = sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], 1, 3)

    assert [r[1] for r in rows] == ["sim-6", "sim-7", "sim-8"]


def test_recent_rows_excludes_other_machines(sim_db):
    sim_db.insert("errors", [("2016-01-01 06:00:00", 2, "other-orig")])
    sim_db.insert("sim_errors", [("2016-01-02 06:00:00", 2, "other-sim"), ("2016-01-02 07:00:00", 1, "mine")])

    rows = sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], 1, 10)

    assert [r[1] for r in rows] == ["mine"]


def test_recent_rows_without_sim_table_returns_original_only(orig_only_db):
    """시뮬레이터를 한 번도 안 켰을 때 - 예외 없이 원본만으로 조용히 동작해야 한다."""
    orig_only_db.insert("errors", [("2016-01-01 06:00:00", 1, "error1")])

    rows = sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], 1, 10)

    assert rows == [("2016-01-01 06:00:00", "error1")]


def test_recent_rows_empty_everywhere_returns_empty_list(sim_db):
    assert sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], 1, 10) == []


# --------------------------------------------------------------------------
# 2. all_rows - 전체 이력 병합 (정비 이력 전용)
# --------------------------------------------------------------------------

def test_all_rows_returns_every_row_merged_and_sorted(sim_db):
    sim_db.insert("maint", [(f"2014-0{m}-01 06:00:00", 1, "comp1") for m in range(1, 7)])
    sim_db.insert("sim_maint", [("2014-03-15 06:00:00", 1, "comp2")])

    rows = sim_query.all_rows("maint", "sim_maint", ["datetime"], 1)

    assert len(rows) == 7, "all_rows는 잘라내지 않고 전부 돌려줘야 한다"
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)
    assert "2014-03-15 06:00:00" in [r[0] for r in rows]


def test_all_rows_excludes_other_machines(sim_db):
    sim_db.insert("maint", [("2014-01-01 06:00:00", 2, "comp1")])
    sim_db.insert("sim_maint", [("2014-02-01 06:00:00", 2, "comp2")])

    assert sim_query.all_rows("maint", "sim_maint", ["datetime"], 1) == []


# --------------------------------------------------------------------------
# 3. dataset_now - 원본 + 시뮬레이션 전체의 '지금'
# --------------------------------------------------------------------------

def test_dataset_now_uses_sim_time_once_it_passes_the_original(sim_db):
    sim_db.insert("telemetry", [("2016-01-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])
    sim_db.insert("sim_telemetry", [("2016-01-05 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])

    assert sim_query.dataset_now() == "2016-01-05 08:00:00"


def test_dataset_now_keeps_original_time_while_sim_is_still_behind(sim_db):
    """sim 테이블이 존재한다고 해서 무조건 sim 값을 쓰면 안 된다 - 둘 중 최대여야 한다."""
    sim_db.insert("telemetry", [("2016-01-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])
    sim_db.insert("sim_telemetry", [("2015-06-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])

    assert sim_query.dataset_now() == "2016-01-01 08:00:00"


def test_dataset_now_without_sim_table_returns_original_max(orig_only_db):
    orig_only_db.insert("telemetry", [("2016-01-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])

    assert sim_query.dataset_now() == "2016-01-01 08:00:00"


def test_dataset_now_returns_none_on_empty_tables(sim_db):
    """MAX()는 빈 테이블에서 None을 돌려준다. 그 None을 걸러내지 않으면 max()가
    TypeError로 터진다 - 완전히 빈 DB(최초 부팅)에서도 None을 반환해야 한다."""
    assert sim_query.dataset_now() is None


# --------------------------------------------------------------------------
# 4. sim_only_now - 실제로 사라졌던 함수
# --------------------------------------------------------------------------

def test_sim_only_now_ignores_original_telemetry(sim_db):
    """docstring에 적힌 2026-09-22 실측 버그의 회귀 테스트.

    원본에는 2016년 데이터가 잔뜩 있고 sim_telemetry는 아직 비어 있는 상태.
    여기서 원본 시각을 돌려주면, 리셋 직후 첫 틱의 기준 시각이 2016년으로 잡히고
    check_recent_failure()가 2015-12-31 원본 고장 기록을 '방금 발생'으로 오인한다
    (#15/#64/#90/#95가 '긴급'으로 재등장한 그 버그).
    """
    sim_db.insert("telemetry", [("2016-01-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])

    assert sim_query.sim_only_now() is None


def test_sim_only_now_returns_sim_max_even_when_original_is_newer(sim_db):
    """dataset_now()로 잘못 위임하면 원본의 더 최신 시각이 새어 들어온다."""
    sim_db.insert("telemetry", [("2016-01-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])
    sim_db.insert("sim_telemetry", [
        ("2015-06-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0),
        ("2015-06-02 08:00:00", 1, 1.0, 1.0, 1.0, 1.0),
    ])

    assert sim_query.sim_only_now() == "2015-06-02 08:00:00"


def test_sim_only_now_returns_none_when_sim_table_missing(orig_only_db):
    """시뮬레이터를 한 번도 안 켠 상태에서 GET /simulator/status가 500이 나면 안 된다."""
    orig_only_db.insert("telemetry", [("2016-01-01 08:00:00", 1, 1.0, 1.0, 1.0, 1.0)])

    assert sim_query.sim_only_now() is None


# --------------------------------------------------------------------------
# 5. sim_only_rows - 원본과 절대 안 섞임
# --------------------------------------------------------------------------

def test_sim_only_rows_never_returns_original_rows(sim_db):
    """시뮬레이터가 추적 중인 설비는 증상 텍스트를 시뮬 데이터만으로 채운다 -
    비어 있으면 비어 있는 채로 둬야지, 원본 옛날 오류로 메우면 안 된다."""
    sim_db.insert("errors", [("2016-01-01 06:00:00", 1, "error1"), ("2016-01-02 06:00:00", 1, "error2")])

    assert sim_query.sim_only_rows("sim_errors", ["datetime", "errorID"], 1, 3) == []


def test_sim_only_rows_returns_newest_limit_in_ascending_order(sim_db):
    sim_db.insert("sim_errors", [(f"2016-01-0{d} 06:00:00", 1, f"e{d}") for d in range(1, 6)])

    rows = sim_query.sim_only_rows("sim_errors", ["datetime", "errorID"], 1, 2)

    assert [r[1] for r in rows] == ["e4", "e5"]


def test_sim_only_rows_limit_zero_returns_nothing_despite_the_unlimited_branch(sim_db):
    """실제 동작을 있는 그대로 못박아 둔다 - 그리고 죽은 분기를 표시해 둔다.

    코드 마지막 줄 `return rows[-limit:] if limit else rows`의 else 쪽은 'limit=0이면
    제한 없음'을 의도한 것으로 읽히지만, 그 위 SQL이 이미 `LIMIT 0`으로 0행을 받아온
    뒤라서 else로 가봐야 빈 리스트다. 즉 이 분기는 절대 결과를 바꾸지 못한다.
    (같은 줄의 `rows[-limit:]` 슬라이스도 SQL이 이미 limit개로 잘라놨으므로 무의미하다 -
    merge를 하는 recent_rows()와 달리 여기서는 두 소스를 합치지 않기 때문이다.)

    '제한 없음'이 진짜 의도라면 SQL을 `LIMIT -1`로 바꿔야 하고, 아니라면 분기를
    지우는 게 맞다. 지금은 호출자가 limit=3만 넘기므로 운영에 영향은 없다.
    """
    sim_db.insert("sim_errors", [(f"2016-01-0{d} 06:00:00", 1, f"e{d}") for d in range(1, 4)])

    assert sim_query.sim_only_rows("sim_errors", ["datetime", "errorID"], 1, 0) == []


def test_sim_only_rows_excludes_other_machines(sim_db):
    sim_db.insert("sim_errors", [("2016-01-01 06:00:00", 2, "other"), ("2016-01-02 06:00:00", 1, "mine")])

    rows = sim_query.sim_only_rows("sim_errors", ["datetime", "errorID"], 1, 10)

    assert [r[1] for r in rows] == ["mine"]


def test_sim_only_rows_missing_table_returns_empty(orig_only_db):
    orig_only_db.insert("errors", [("2016-01-01 06:00:00", 1, "error1")])

    assert sim_query.sim_only_rows("sim_errors", ["datetime", "errorID"], 1, 3) == []


# --------------------------------------------------------------------------
# 6. machine_has_sim_failure - '지금 시뮬레이터가 추적 중인 설비'인가
# --------------------------------------------------------------------------

def test_machine_has_sim_failure_true_after_sim_failure(sim_db):
    sim_db.insert("sim_failures", [("2016-01-02 06:00:00", 1, "comp1")])

    assert sim_query.machine_has_sim_failure(1) is True


def test_machine_has_sim_failure_ignores_original_failures(sim_db):
    """원본 고장 기록이 있다고 시뮬레이터가 추적 중인 설비가 되는 건 아니다."""
    sim_db.insert("failures", [("2015-12-31 06:00:00", 1, "comp4")])

    assert sim_query.machine_has_sim_failure(1) is False


def test_machine_has_sim_failure_is_per_machine(sim_db):
    sim_db.insert("sim_failures", [("2016-01-02 06:00:00", 2, "comp1")])

    assert sim_query.machine_has_sim_failure(2) is True
    assert sim_query.machine_has_sim_failure(1) is False


def test_machine_has_sim_failure_false_when_table_missing(orig_only_db):
    assert sim_query.machine_has_sim_failure(1) is False
