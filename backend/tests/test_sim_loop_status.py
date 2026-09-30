"""sim_loop.run_forever의 오류 상태(last_error/consecutive_failures) 처리 - 2026-09-30
code-quality-reviewer가 실행으로 재현한 결함의 회귀: (1) 지우는 쓰기가 실패해도 메모리 카운터가
먼저 0이 돼서 화면의 오류 배너가 영구히 남음, (2) 재기동 후에도 옛 오류 상태가 안 지워짐,
(3) 두 키를 따로 써서 어긋남. 실제 SQLite 대신 제어값 저장소를 가짜로 바꿔 결정론적으로 검증한다."""
import asyncio
import logging

import pytest

from data import sim_loop


class _Control:
    """_set_controls의 가짜 저장소 - 호출 이력과 최종 상태를 남기고, 지정한 호출 번호에서 실패시킨다."""

    def __init__(self, fail_on_calls=(), always_fail=False, initial=None):
        self.state = dict(initial or {})
        self.calls: list[dict] = []
        self.fail_on_calls = set(fail_on_calls)
        self.always_fail = always_fail

    def __call__(self, values):
        self.calls.append(dict(values))
        if self.always_fail or len(self.calls) in self.fail_on_calls:
            raise RuntimeError("database is locked")
        self.state.update(values)


class _StopLoop(BaseException):
    """run_forever의 `except Exception`이 잡지 못하는 종료 신호 - 취소를 삼켜서 계속 도는 (버그가 있는)
    루프도 테스트 정리 단계에서 스스로 끝나게 한다."""


def _run_loop(monkeypatch, control, tick, duration=0.35):
    """루프를 duration 동안 돌리고 취소한다. 취소를 삼키는 버그가 생겨도 이 헬퍼가 `await task`에서
    영원히 기다리지 않도록 종료 신호를 쓴다 - 2026-09-30 취소 삼킴 변이를 반복해서 돌렸더니 테스트가
    실패로 끝나지 않고 3~4번에 1번은 멈췄다(원인: 다른 테스트들이 공유하는 이 헬퍼의 `await task`)."""
    logging.disable(logging.CRITICAL)
    state = {"stop": False}

    def is_running():
        if state["stop"]:
            raise _StopLoop()
        return True

    monkeypatch.setattr(sim_loop, "SIM_TICK_SECONDS", 0.02)
    monkeypatch.setattr(sim_loop, "is_running", is_running)
    monkeypatch.setattr(sim_loop, "_tick_once", tick)
    monkeypatch.setattr(sim_loop, "_set_controls", control)

    async def go():
        task = asyncio.create_task(sim_loop.run_forever())
        await asyncio.sleep(duration)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=2)
        if task not in done:           # 취소가 삼켜졌다 - 멈추는 대신 종료 신호로 끝낸다
            state["stop"] = True
            await asyncio.wait({task}, timeout=2)
            raise AssertionError("취소가 삼켜져서 run_forever가 계속 돈다")
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()

    try:
        asyncio.run(go())
    finally:
        logging.disable(logging.NOTSET)


def _failing_once():
    ticks = {"n": 0}

    def tick():
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise RuntimeError("boom")
    return tick, ticks


def test_failure_is_recorded_atomically_then_cleared_on_recovery(monkeypatch):
    control = _Control()
    tick, _ = _failing_once()
    _run_loop(monkeypatch, control, tick)

    # 호출 순서: 기동 초기화 -> 실패 기록 -> 복구 초기화 (두 키가 항상 한 번에 쓰인다)
    assert control.calls[0] == {"last_error": "", "consecutive_failures": "0"}
    assert control.calls[1] == {"last_error": "RuntimeError: boom", "consecutive_failures": "1"}
    assert control.calls[2] == {"last_error": "", "consecutive_failures": "0"}
    assert all(set(c) == {"last_error", "consecutive_failures"} for c in control.calls)
    assert control.state == {"last_error": "", "consecutive_failures": "0"}


def test_failed_clear_is_retried_on_the_next_good_tick(monkeypatch):
    """회귀: 예전엔 카운터를 먼저 0으로 만들어서, 지우는 쓰기가 한 번 실패하면 이후 정상 틱이
    다시는 지우려 하지 않아 오류 배너가 영구히 남았다(3번째 호출 = 첫 복구 초기화를 실패시킴)."""
    control = _Control(fail_on_calls={3})
    tick, _ = _failing_once()
    _run_loop(monkeypatch, control, tick)

    assert len(control.calls) >= 4, "실패한 복구 초기화를 다음 정상 틱에서 다시 시도해야 한다"
    assert control.state == {"last_error": "", "consecutive_failures": "0"}


def test_startup_clears_stale_error_state_left_by_a_previous_run(monkeypatch):
    control = _Control(initial={"last_error": "RuntimeError: old", "consecutive_failures": "7"})
    _run_loop(monkeypatch, control, tick=lambda: None)

    assert control.state == {"last_error": "", "consecutive_failures": "0"}


def test_loop_survives_when_status_writes_keep_failing(monkeypatch):
    """상태 기록이 계속 실패해도(DB 잠금 등) 루프는 죽지 않고 틱을 이어가야 한다 - 예전 HEAD는
    이 경우 OperationalError로 루프가 죽었다."""
    control = _Control(always_fail=True)
    tick, ticks = _failing_once()
    _run_loop(monkeypatch, control, tick)

    assert ticks["n"] >= 5, "상태 기록 실패 뒤에도 틱이 계속 돌아야 한다"


def test_cancellation_while_inside_the_thread_hop_propagates(monkeypatch):
    """to_thread로 옮긴 await 안에서 취소가 삼켜지지 않는지. 예전 테스트는 취소가 try 밖의 sleep에
    떨어져서 `except CancelledError: continue` 변이도 통과시켰고, 그 변이는 전체 스위트를 600초 동안
    멈추게 했다(2026-09-30 test-engineer). 여기서는 is_running이 스레드 안에서 막혀 있는 동안(=try 안의
    await) 취소하고, 멈추는 대신 시간 제한 안에 끝났는지를 단언으로 확인한다. 또 그 단언이 실패한 경우에도
    남아 있는 루프가 정리 단계에서 asyncio.run을 붙잡지 않도록 종료 신호를 심어 둔다(같은 변이를 8번
    돌렸더니 실패는 매번 잡히는데 3번은 정리 단계에서 멈췄다)."""
    import threading

    entered, release = threading.Event(), threading.Event()
    state = {"n": 0, "stop": False}

    def is_running():
        if state["stop"]:
            raise _StopLoop()
        state["n"] += 1
        if state["n"] == 1:
            entered.set()
            release.wait(5)
        return True

    monkeypatch.setattr(sim_loop, "SIM_TICK_SECONDS", 0.02)
    monkeypatch.setattr(sim_loop, "is_running", is_running)
    monkeypatch.setattr(sim_loop, "_tick_once", lambda: None)
    monkeypatch.setattr(sim_loop, "_set_controls", _Control())

    async def go():
        task = asyncio.create_task(sim_loop.run_forever())
        try:
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()          # 루프는 지금 try 안의 `await to_thread(is_running)`에 있다
            release.set()
            done, _ = await asyncio.wait({task}, timeout=2)
            assert task in done, "취소가 삼켜져서 run_forever가 계속 돈다"
            assert task.cancelled()
        finally:
            state["stop"] = True   # 버그가 있어 아직 도는 루프라도 다음 반복에서 끝나게 한다
            release.set()
            await asyncio.wait({task}, timeout=2)
            if task.done() and not task.cancelled():
                task.exception()   # _StopLoop을 '가져간 것'으로 처리해 경고를 남기지 않는다

    asyncio.run(go())


def test_set_controls_writes_both_keys_atomically_to_real_sqlite(tmp_path, monkeypatch):
    """가짜 저장소가 아니라 실제 SQLite로: (1) 커밋이 실제로 일어나 새 연결에서 읽힌다, (2) 도중에 실패하면
    앞의 키도 남지 않는다(한 트랜잭션). 예전 테스트는 _set_controls를 통째로 바꿔서 두 변이(키마다 커밋,
    커밋 누락)가 모두 살아남았다(2026-09-30 test-engineer)."""
    import sqlite3

    db = str(tmp_path / "ctl.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sim_control (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(sim_loop, "DB_PATH", db)

    sim_loop._set_controls({"last_error": "E", "consecutive_failures": "3"})
    rows = dict(sqlite3.connect(db).execute("SELECT key, value FROM sim_control").fetchall())
    assert rows == {"last_error": "E", "consecutive_failures": "3"}

    with pytest.raises(sqlite3.Error):
        sim_loop._set_controls({"last_error": "NEW", "consecutive_failures": {"지원하지 않는 값": 1}})
    rows = dict(sqlite3.connect(db).execute("SELECT key, value FROM sim_control").fetchall())
    assert rows == {"last_error": "E", "consecutive_failures": "3"}, "도중 실패인데 앞의 키가 갱신됨(원자적이지 않음)"
