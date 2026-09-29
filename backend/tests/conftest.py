"""pytest 공유 fixture. uvicorn과 동일하게 sys.path에 backend/가 잡혀 있어야
`from data import event_store` 같은 절대 임포트가 동작한다 - 별도 pytest.ini/
pyproject.toml 설정 없이, 바로 아래 sys.path.insert()로 이 파일이 직접 보장한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _no_real_external_calls(monkeypatch):
    """2026-09-29 code-quality-reviewer 지적(H2, 실행으로 확인): notify.py/
    cmms_client.py가 import 시점에 load_dotenv()로 개발자의 실제 .env 값을 모듈
    상수로 고정해버려서, 이 값들을 직접 세팅하지 않는 테스트도 개발자의 진짜
    Slack 웹훅·CMMS 자격증명을 그대로 물려받는다 - 실제로 테스트를 로컬에서
    돌리면 진짜 Slack 채널에 메시지가 올라가고 진짜 Atlas에 작업지시서가
    생성된다(finalize_node가 실패를 삼켜서 테스트 자체는 계속 초록불로 보임).
    모든 테스트에 기본으로 이 값들을 비워서, 실제로 외부 호출을 테스트하려는
    케이스만 자기 안에서 명시적으로 monkeypatch.setattr로 다시 채우게 한다."""
    import cmms_client
    import notify
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", None, raising=False)
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", None, raising=False)
    monkeypatch.setattr(cmms_client, "CMMS_MCP_TOKEN", None, raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


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
