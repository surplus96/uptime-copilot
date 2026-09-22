"""시뮬레이터 1단계(sim_engine.py 상태 머신 / sim_store.py 저장소) 회귀 테스트.
2026-09-22 실측 근거(SIMULATOR_PLAN.md "실측 근거" 표)를 코드가 실제로 지키는지 확인한다.
각 테스트는 대응하는 규칙을 코드에서 지웠을 때 실패하는 것까지 직접 검증했다
(변이 테스트 - SIMULATOR_PLAN.md 진행 상태 1번 참고)."""
import random

import pytest

from data import sim_engine as E
from data import sim_store


def _run(seed=1, hours=8760, machines=100):
    rng = random.Random(seed)
    ms = [E.MachineSim(i) for i in range(1, machines + 1)]
    log = []
    for t in range(hours):
        for m in ms:
            before = (m.state, m.elapsed, m.lead_hours)
            r = E.step(m, rng, t % 24)
            log.append((t, (m.state, m.drift_sigma), before, r))
    return log


def test_failure_only_at_06():
    for t, _, _, r in _run():
        if r["failure"]:
            assert t % 24 == E.FAILURE_HOUR


def test_failure_needs_full_lead_time():
    for t, _, (state, elapsed, lead), r in _run(seed=2):
        if r["failure"]:
            assert elapsed + 1 >= lead


def test_maint_matches_failure_component():
    seen = 0
    for _, _, _, r in _run(seed=3):
        assert (r["failure"] is None) == (r["maint"] is None)
        if r["failure"]:
            seen += 1
            assert r["maint"] == r["failure"]
    assert seen > 0


def test_only_allowed_state_transitions():
    allowed = {("HEALTHY", "DEGRADING"), ("DEGRADING", "FAULT"), ("FAULT", "HEALTHY"), ("DEGRADING", "HEALTHY")}
    for _, (after, _), (state, _, _), _ in _run(seed=4):
        if after != state:
            assert (state, after) in allowed


def test_precursor_error_moves_to_fault(monkeypatch):
    monkeypatch.setattr(E, "P_PRECURSOR_ERROR", 1.0)
    monkeypatch.setattr(E, "P_BG_ERROR", 0.0)
    m = E.MachineSim(1, state="DEGRADING", signal="volt", direction=1, drift_sigma=1.5, lead_hours=48)
    r = E.step(m, random.Random(0), 10)
    assert m.state == "FAULT" and m.errors_emitted == 1 and len(r["errors"]) == 1


def test_drift_ramps_then_holds_on_target_signal_only(monkeypatch):
    monkeypatch.setattr(E, "P_SPIKE", 0.0)
    monkeypatch.setattr(E, "P_PRECURSOR_ERROR", 0.0)
    m = E.MachineSim(1, state="DEGRADING", signal="rotate", direction=-1, drift_sigma=2.0, lead_hours=48)
    rng = random.Random(0)
    values = []
    for h in range(1, 13):
        r = E.step(m, rng, 10)
        assert set(r["offsets"]) == {"rotate"}
        values.append(r["offsets"]["rotate"])
    assert all(a > b for a, b in zip(values[:E.RAMP_UP_HOURS], values[1:E.RAMP_UP_HOURS]))
    assert abs(values[0]) < 2.0
    assert values[E.RAMP_UP_HOURS - 1] == pytest.approx(-2.0)
    assert values[-1] == pytest.approx(-2.0)


def test_weak_drift_stays_in_measured_range(monkeypatch):
    monkeypatch.setattr(E, "STRONG_FRACTION", 0.0)
    monkeypatch.setattr(E, "DRIFT_STD", 5.0)
    drifts = [abs(d) for _, (after, d), (s, _, _), _ in _run(seed=5, hours=3000) if s == "HEALTHY" and after == "DEGRADING"]
    assert drifts and all(0.8 <= d <= 2.6 for d in drifts)
    assert min(drifts) == 0.8 or max(drifts) == 2.6


def test_single_hour_spike_is_diluted_below_detection_threshold(monkeypatch):
    monkeypatch.setattr(E, "P_SPIKE", 1.0)
    m = E.MachineSim(1)
    r = E.step(m, random.Random(0), 10)
    assert len(r["offsets"]) == 1 and abs(next(iter(r["offsets"].values()))) == E.SPIKE_SIGMA
    assert E.SPIKE_SIGMA / 24 < 3.0


def test_same_seed_reproducible():
    a = [(t, r["failure"], tuple(r["errors"])) for t, _, _, r in _run(seed=7, hours=2000)]
    b = [(t, r["failure"], tuple(r["errors"])) for t, _, _, r in _run(seed=7, hours=2000)]
    assert a == b


def test_store_roundtrip_and_idempotent_init(tmp_path, monkeypatch):
    """실제 pdm_telemetry.db는 절대 건드리지 않도록 DB_PATH를 임시 파일로 바꿔치기한다
    (conftest.py의 event_store_module 패턴과 동일)."""
    monkeypatch.setattr(sim_store, "DB_PATH", str(tmp_path / "t.db"))
    sim_store.init_sim_tables()
    sim_store.init_sim_tables()  # 재실행 안전(IF NOT EXISTS) 확인
    states = sim_store.load_states()
    assert len(states) == 100 and all(m.state == "HEALTHY" for m in states.values())
    rng = random.Random(3)
    for t in range(3000):
        for m in states.values():
            E.step(m, rng, t % 24)
    assert any(m.state != "HEALTHY" for m in states.values())
    sim_store.save_states(states)
    assert sim_store.load_states() == states
