"""pytest 공유 fixture. uvicorn과 동일하게 sys.path에 backend/가 잡혀 있어야
`from data import event_store` 같은 절대 임포트가 동작한다 - 별도 pytest.ini/
pyproject.toml 설정 없이, 바로 아래 sys.path.insert()로 이 파일이 직접 보장한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def event_store_module(tmp_path, monkeypatch):
    """실제 pdm_telemetry.db를 절대 건드리지 않도록, DB_PATH를 테스트마다 새로 만드는
    임시 파일로 바꿔치기한 event_store 모듈을 반환한다."""
    import sqlite3

    from data import event_store, sim_query

    db_path = str(tmp_path / "test_events.db")
    monkeypatch.setattr(event_store, "DB_PATH", db_path)
    # _dataset_now()는 이제 sim_query.dataset_now()에 위임한다(2026-09-22 시뮬레이터
    # 연동) - sim_query의 DB_PATH도 같이 바꿔치기하지 않으면 이 fixture가 실제
    # pdm_telemetry.db를 그대로 읽어버린다(2026-09-22 test-engineer 발견).
    monkeypatch.setattr(sim_query, "DB_PATH", db_path)
    event_store.init_event_table()

    # complete_events()가 내부적으로 _dataset_now()로 telemetry 테이블을 조회한다 -
    # 실제 스키마를 흉내낸 최소 테이블 하나만 만들어둔다(값 자체는 이 테스트들의
    # 관심사가 아니라서 임의의 한 행이면 충분).
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE telemetry (datetime TEXT)")
    conn.execute("INSERT INTO telemetry VALUES ('2016-01-01 09:00:00')")
    conn.commit()
    conn.close()

    return event_store
