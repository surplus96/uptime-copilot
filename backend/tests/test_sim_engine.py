"""시뮬레이터 1단계(sim_engine.py 상태 머신 / sim_store.py 저장소) 회귀 테스트.
2026-09-22 실측 근거(docs/design/SIMULATOR_PLAN.md "실측 근거" 표)를 코드가 실제로 지키는지 확인한다.
각 테스트는 대응하는 규칙을 코드에서 지웠을 때 실패하는 것까지 직접 검증했다
(변이 테스트 - docs/design/SIMULATOR_PLAN.md 진행 상태 1번 참고)."""
import random

import pandas as pd
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


# ---- 2026-10-01: 전조 오류를 원본 데이터의 고장 직전 패턴(서명)으로 -------------------------------
# 근거: 원본 고장의 직전 72시간 오류를 직접 집계(2026-10-01, docs/decisions.md).
#   - 오류의 88%가 고장 24~36시간 전에 몰려 있고, 대부분 정확히 24시간 전이다.
#   - 부품별 서명이 뚜렷하다: comp1<-error1, comp2<-error2+error3, comp3<-error4, comp4<-error5
#     (부품당 고장 1건에 서명 오류가 평균 약 1개씩).
# 이 기대표는 코드(E.SIGNATURE_ERRORS)와 별개로 여기에 다시 적는다 - 같은 표를 코드에서 가져다
# 비교하면 표가 틀려도 테스트가 통과한다.
EXPECTED_SIGNATURE = {
    "comp1": ["error1"],
    "comp2": ["error2", "error3"],
    "comp3": ["error4"],
    "comp4": ["error5"],
}


def _episodes(seed=11, hours=8760 * 2, machines=100):
    """고장까지 간 에피소드마다 (부품, 고장 시각, {시각: 그 시간 오류 목록})을 모은다."""
    rng = random.Random(seed)
    ms = [E.MachineSim(i) for i in range(1, machines + 1)]
    history: dict[int, dict[int, list[str]]] = {m.machine_id: {} for m in ms}
    episodes = []
    for t in range(hours):
        for m in ms:
            r = E.step(m, rng, t % 24)
            if r["errors"]:
                history[m.machine_id][t] = list(r["errors"])
            if r["failure"]:
                episodes.append((r["failure"], t, history[m.machine_id]))
                history[m.machine_id] = {}
    return episodes


def test_signature_error_types_match_the_component_at_exactly_24h():
    """서명이 있는 고장은 고장 정확히 24시간 전에 그 부품의 서명 오류가 전부 있다."""
    episodes = _episodes()
    assert len(episodes) > 300
    with_signature = [
        (comp, errs.get(t_fail - 24, [])) for comp, t_fail, errs in episodes
        if all(need in errs.get(t_fail - 24, []) for need in EXPECTED_SIGNATURE[comp])
    ]
    # 서명이 없는 고장(조용한 고장)은 아래 테스트가 비율로 따로 본다 - 여기서는 있는 쪽의 종류만 확인
    assert len(with_signature) > 0.9 * len(episodes)


def test_signature_comes_only_at_exactly_24h_before_failure():
    """실제 데이터는 서명이 정확히 24시간 전에만 나온다(고장의 95~99%에서 그 시각에 서명 오류가 전부 있다)
    - 25~36시간 전에 흩어지지 않는다. 처음엔 "24~36시간 전 88%"를 근거로 22%를 흩어 놓았는데, 정확히 24시간
    전인 비율을 직접 세어 보니 거의 전부였다."""
    episodes = _episodes()
    exact = sum(
        1 for comp, t_fail, errs in episodes
        if all(need in errs.get(t_fail - 24, []) for need in EXPECTED_SIGNATURE[comp])
    )
    assert 0.94 <= exact / len(episodes) <= 0.995, exact / len(episodes)
    # 25~36시간 전 구간에는 (배경 오류를 빼면) 서명이 나오지 않는다 - 배경 오류가 우연히 같은 종류일 확률 정도만
    early = sum(
        1 for comp, t_fail, errs in episodes
        if all(any(need in errs.get(t_fail - lag, []) for lag in range(25, 37)) for need in EXPECTED_SIGNATURE[comp])
    )
    assert early / len(episodes) < 0.05, early / len(episodes)


# 조용한 고장(서명 없는 고장)의 실측 비율: 1 - 서명 완비율(24~36시간 전 구간에서 서명 오류가 전부 있는 고장 비율)
EXPECTED_SILENT = {"comp1": 0.047, "comp2": 0.012, "comp3": 0.023, "comp4": 0.017}


def test_silent_failures_follow_the_measured_rate_per_component():
    """서명 없이 고장나는 비율도 실제와 같아야 한다 - 0%면 주의 없이 긴급으로 넘어가는 경우가 시뮬레이션에
    아예 없는 셈이고, 한 부품에 몰리면 그 부품만 주의가 안 뜬다."""
    episodes = _episodes(hours=8760 * 6)
    for comp, expected in EXPECTED_SILENT.items():
        mine = [(t_fail, errs) for c, t_fail, errs in episodes if c == comp]
        assert len(mine) > 500
        silent = sum(1 for t_fail, errs in mine if not all(n in errs.get(t_fail - 24, []) for n in EXPECTED_SIGNATURE[comp]))
        assert abs(silent / len(mine) - expected) < 0.025, (comp, silent / len(mine))


def test_extra_random_errors_do_not_suppress_the_signature(monkeypatch):
    """무작위 잡음 오류가 먼저 나서 상태가 FAULT가 돼도 서명 오류는 그대로 나와야 한다."""
    monkeypatch.setattr(E, "P_PRECURSOR_ERROR", 1.0)
    monkeypatch.setattr(E, "P_BG_ERROR", 0.0)
    m = E.MachineSim(1, state="DEGRADING", signal="rotate", direction=1, drift_sigma=1.5, lead_hours=48)
    rng = random.Random(0)
    errs: dict[int, list[str]] = {}
    t_fail = None
    for t in range(1, 200):
        r = E.step(m, rng, (10 + t) % 24)
        errs[t] = r["errors"]
        if r["failure"]:
            t_fail = t
            break
    assert t_fail is not None
    window = [e for t, es in errs.items() if t_fail - 36 <= t <= t_fail - 24 for e in es]
    assert "error2" in window and "error3" in window


def test_hours_until_failure_matches_what_actually_happens():
    """서명 시각을 정하는 계산(hours_until_failure)이 실제 고장까지 남은 시간과 어긋나면 안 된다."""
    rng = random.Random(21)
    checked = 0
    for start_hour in range(24):
        m = E.MachineSim(1, state="DEGRADING", signal="volt", direction=1, drift_sigma=1.5, lead_hours=44 + start_hour % 9)
        predicted = {}
        for t in range(1, 200):
            hour = (start_hour + t) % 24
            # step() 안에서 elapsed가 올라간 뒤에 쓰이는 값이므로 같은 기준으로 맞춘다
            predicted[t] = E.hours_until_failure(m.elapsed + 1, m.lead_hours, hour)
            r = E.step(m, rng, hour)
            if r["failure"]:
                for tt, p in predicted.items():
                    assert p == t - tt, (start_hour, tt, p, t)
                    checked += 1
                break
    assert checked > 24 * 40


# ---- 2026-10-01: 열화 편차의 방향·크기를 신호별 실측값으로 ------------------------------------------
# 근거: 원본 고장 직전 24시간 평균의 (자기 설비 기준) z값 - 부품의 대응 신호만 일관되게 벗어난다.
#   volt(comp1) +1.28 / rotate(comp2) -1.46 / pressure(comp3) +2.10 / vibration(comp4) +1.80
#   고장의 100%에서 방향이 같다(rotate만 음수). 다른 신호는 평균 |z| 0.27로 사실상 정상.
# 예전엔 방향을 66% 확률로 무작위로 뽑았다 - 4개 신호를 한데 묶어 센 값이라 신호별 방향 정보를
# 잃은 것이고, 위험도 모델은 이 방향에 반응하므로(rotate가 올라가는 열화는 경보 0%)
# 열화가 주의로 이어지지 않았다.
EXPECTED_DIRECTION = {"volt": 1, "rotate": -1, "pressure": 1, "vibration": 1}
EXPECTED_DRIFT_MEAN = {"volt": 1.28, "rotate": 1.46, "pressure": 2.10, "vibration": 1.80}


def _episode_starts(seed=31, hours=8760 * 3, machines=100):
    rng = random.Random(seed)
    ms = [E.MachineSim(i) for i in range(1, machines + 1)]
    starts = []
    for t in range(hours):
        for m in ms:
            was_healthy = m.state == "HEALTHY"
            E.step(m, rng, t % 24)
            if was_healthy and m.state != "HEALTHY":
                starts.append((m.signal, m.direction, m.drift_sigma))
    return starts


def test_drift_direction_is_a_property_of_the_signal():
    starts = _episode_starts()
    assert len(starts) > 1000
    assert {s for s, _, _ in starts} == set(EXPECTED_DIRECTION)
    for signal, direction, _ in starts:
        assert direction == EXPECTED_DIRECTION[signal], signal


def test_drift_size_follows_measured_mean_per_signal(monkeypatch):
    monkeypatch.setattr(E, "STRONG_FRACTION", 0.0)  # 데모용 강한 열화는 따로 - 여기선 실측 분포만 본다
    starts = _episode_starts()
    for signal, expected in EXPECTED_DRIFT_MEAN.items():
        sizes = [abs(d) for s, _, d in starts if s == signal]
        assert len(sizes) > 200
        assert abs(sum(sizes) / len(sizes) - expected) < 0.15, (signal, sum(sizes) / len(sizes))


def test_signature_hour_marks_fault_and_counts_its_errors(monkeypatch):
    """서명 오류가 난 시간에 상태가 FAULT가 되고 errors_emitted가 서명 개수만큼 늘어야 한다 - 화면의
    '전조 오류 발생'/'전조 오류 N건' 표시(streamlit_app)가 이 두 값만 본다. 잡음 오류를 끄면 서명이
    유일한 오류 출처라, 이 줄들을 지우면 열화가 고장까지 '열화 중·오류 0건'으로만 보인다."""
    monkeypatch.setattr(E, "P_PRECURSOR_ERROR", 0.0)
    monkeypatch.setattr(E, "P_BG_ERROR", 0.0)
    m = E.MachineSim(1, state="DEGRADING", signal="rotate", direction=-1, drift_sigma=1.5, lead_hours=48)
    rng = random.Random(0)
    for t in range(1, 200):
        r = E.step(m, rng, (10 + t) % 24)
        if r["errors"]:
            assert sorted(r["errors"]) == EXPECTED_SIGNATURE["comp2"]
            assert m.state == "FAULT" and m.errors_emitted == 2
            return
        assert m.state == "DEGRADING" and not r["failure"]
    raise AssertionError("서명 오류가 고장 전에 나오지 않았다")


def test_non_signature_errors_before_failure_are_only_background_errors():
    """고장 직전 72시간의 서명 아닌 오류는 실제로 고장당 약 0.24건인데 이는 배경 오류율 x 72시간(0.226)과
    같다 - 열화 중에만 따로 나는 잡음 오류는 없다. 0이면 배경 오류가 없고, 예전 값(시간당 0.007, 합쳐 약 0.8)이면
    실제보다 많다."""
    for seed in (11, 12, 13):
        episodes = _episodes(seed=seed)
        extras = []
        for comp, t_fail, errs in episodes:
            window = [e for t, es in errs.items() if t_fail - 72 <= t <= t_fail for e in es]
            sig = list(EXPECTED_SIGNATURE[comp])
            for need in sig:
                if need in window:
                    window.remove(need)
            extras.append(len(window))
        assert 0.15 <= sum(extras) / len(extras) <= 0.35, (seed, sum(extras) / len(extras))


def test_background_error_rate_matches_the_measured_rate(monkeypatch):
    """정상 설비의 배경 오류율: 실제 3930건 중 고장 직전 72시간 밖 2747건 = 설비·시간당 0.00314."""
    monkeypatch.setattr(E, "P_EPISODE", 0.0)  # 열화 에피소드의 서명 오류가 섞이지 않게 - 정상 설비만 본다
    rng = random.Random(3)
    machines = [E.MachineSim(i) for i in range(1, 101)]
    n = 0
    hours = 2000
    for t in range(hours):
        for m in machines:
            n += len(E.step(m, rng, t % 24)["errors"])
    assert 0.0027 <= n / (hours * 100) <= 0.0036, n / (hours * 100)


@pytest.mark.parametrize("signal", sorted(EXPECTED_DIRECTION))
def test_inject_uses_the_measured_direction_of_the_signal(tmp_path, monkeypatch, signal):
    """데모 버튼(sim_loop.inject)도 엔진과 같은 신호별 방향을 써야 한다 - 예전처럼 +1로 고정하면
    rotate 주입은 회전수가 '올라가는' 열화가 되고, 위험도 모델은 그 방향에 경보를 내지 않는다
    (주의 없이 바로 긴급). 실제 DB 대신 임시 파일을 쓴다."""
    from data import sim_loop

    db = str(tmp_path / "t.db")
    monkeypatch.setattr(sim_store, "DB_PATH", db)
    monkeypatch.setattr(sim_loop, "DB_PATH", db)
    sim_store.init_sim_tables()

    sim_loop.inject(7, signal)

    m = sim_store.load_states()[7]
    assert (m.state, m.signal, m.direction) == ("DEGRADING", signal, EXPECTED_DIRECTION[signal])


# ---- 2026-10-01: 시뮬레이션에 정비 이력 심기 ----------------------------------------------------
# 라이브 시뮬레이션에는 정비 기록이 없어서 위험 모델의 "마지막 정비 후 경과시간" 피처가 약 9만 시간
# (학습 범위 최대 약 1만 시간 밖)이었다. 실제 고장 764건의 직전 정비 후 경과시간 분포(10% 약 900 /
# 중앙 1800 / 90% 약 4200시간)에서 설비·부품마다 한 번 뽑아 "시뮬레이션 시작 전의 마지막 정비"로 심는다.
EXPECTED_AGE_QUANTILES = {0.21: 300, 0.369: 600, 0.492: 900, 0.725: 1800, 0.87: 3000}  # 5000시간 이상 꼬리는 지수 근사가 약간 다르다(문서 참고)


def test_initial_ages_follow_the_measured_age_distribution_and_share_events():
    """부품 나이 분포(임의의 시점, 시간 가중)는 실제 정비 기록의 위험 노출 시간 누적 비율과 같아야 하고,
    부품들은 같은 정비 사건을 공유한다 - 같은 사건에서 정비된 부품은 나이가 정확히 같다."""
    rng = random.Random(5)
    machines = [E.draw_initial_ages(rng) for _ in range(20000)]
    pooled = sorted(a for ages in machines for a in ages.values())
    for q, expected in EXPECTED_AGE_QUANTILES.items():
        got = pooled[int(q * len(pooled))]
        # 사건 과정(지수 간격)은 실제보다 상위 구간이 10~17% 젊다(측정: 300/600/900/1200/1800/3000 -> 288/558/820/1079/1563/2482)
        # - 실제 사건 간격은 360시간 단위로 양자화돼 있고 꼬리가 더 두껍다. 근사로 받아들이는 범위.
        assert abs(got - expected) / expected < 0.2, (q, got)
    tied = sum(1 for ages in machines if len({round(a, 6) for a in ages.values()}) < 4)
    assert tied / len(machines) > 0.25  # 독립으로 뽑으면 0 - 마지막 사건이 부품 2개를 정비했을 확률(약 1/3)


@pytest.fixture
def seed_db(tmp_path, monkeypatch):
    from data import sim_loop

    db = str(tmp_path / "seed.db")
    monkeypatch.setattr(sim_store, "DB_PATH", db)
    monkeypatch.setattr(sim_loop, "DB_PATH", db)
    sim_store.init_sim_tables()
    return db


def _maint_rows(db):
    import sqlite3

    conn = sqlite3.connect(db)
    rows = conn.execute('SELECT datetime, "machineID", comp FROM sim_maint').fetchall()
    conn.close()
    return rows


def _seed(db, base="2026-10-01 00:00:00", machines=range(1, 101)):
    import sqlite3

    import pandas as pd

    from data import sim_loop

    conn = sqlite3.connect(db)
    sim_loop._seed_maintenance_history(conn, pd.Timestamp(base), machines)
    conn.commit()
    conn.close()


def test_seed_gives_every_machine_and_component_a_past_maintenance(seed_db):
    _seed(seed_db)

    rows = _maint_rows(seed_db)
    assert len(rows) == 400
    assert {(m, c) for _, m, c in rows} == {(m, f"comp{i}") for m in range(1, 101) for i in range(1, 5)}
    assert all(r[0] < "2026-10-01 00:00:00" for r in rows)  # 전부 시뮬레이션 시작 전


def test_seed_is_idempotent_and_skips_components_that_already_have_a_maintenance(seed_db):
    import sqlite3

    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_maint VALUES ('2026-09-30 06:00:00', 7, 'comp2')")  # 이미 있는 고장 정비
    conn.commit()
    conn.close()

    _seed(seed_db)
    _seed(seed_db)

    rows = _maint_rows(seed_db)
    assert len(rows) == 400
    assert [r for r in rows if r[1] == 7 and r[2] == "comp2"] == [("2026-09-30 06:00:00", 7, "comp2")]


def test_seed_is_reproducible_for_the_same_sim_seed_and_differs_across_seeds(seed_db, monkeypatch):
    from data import sim_loop

    _seed(seed_db)
    first = sorted(_maint_rows(seed_db))
    import sqlite3

    conn = sqlite3.connect(seed_db)
    conn.execute("DELETE FROM sim_maint")
    conn.commit()
    conn.close()
    _seed(seed_db)
    assert sorted(_maint_rows(seed_db)) == first

    conn = sqlite3.connect(seed_db)
    conn.execute("DELETE FROM sim_maint")
    conn.commit()
    conn.close()
    monkeypatch.setattr(sim_loop, "SIM_SEED", 43)
    _seed(seed_db)
    assert sorted(_maint_rows(seed_db)) != first


def test_tick_seeds_maintenance_history_on_the_first_run(seed_db, monkeypatch):
    """_tick_once가 실제로 심는지 - 함수가 있어도 호출이 빠지면 라이브는 예전(9만 시간)으로 돌아간다."""
    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))

    sim_loop._tick_once()

    assert len(_maint_rows(seed_db)) >= 400


# ---- 2026-10-01: 어린 부품은 열화를 시작하지 않는다 --------------------------------------------------
# [정정: 아래 하한은 이후 부품별 1080/720h로 바뀌었고, "모델이 어린 부품에 약하다"는 해석은 틀렸다 - docs/decisions.md '후속 3']
# 정비 이력을 심은 뒤 브라우저 테스트에서 #33(진동, comp4)이 주의 없이 바로 긴급이 됐다. 시간별로 재생해 보니
# comp4를 정비한 지 약 630시간밖에 안 된 설비였고 위험 점수가 내내 0.036이었다. 경과시간 구간별로 쟀더니
# 경보 실패가 900시간 미만에 몰려 있었다(volt 1/18, vibration 2/14 경보 / 900시간 이상은 거의 100%).
# 실제 데이터에서도 고장 직전 정비 후 경과시간의 10%가 약 900시간 아래라 어린 부품의 고장은 드물다 -
# 시뮬레이터가 부품 나이와 무관하게 열화를 시작하던 것이 어긋난 지점이다.
def _healthy_machine_after_steps(monkeypatch, ages, steps=30):
    monkeypatch.setattr(E, "P_EPISODE", 1.0)
    monkeypatch.setattr(E, "SIGNALS", ["vibration"])  # 신호를 고정해서 어느 부품의 나이가 쓰이는지 분명하게
    m = E.MachineSim(1)
    rng = random.Random(0)
    for _ in range(steps):
        E.step(m, rng, 10, comp_ages=ages)
    return m


def test_episode_does_not_start_on_a_component_younger_than_the_floor(monkeypatch):
    m = _healthy_machine_after_steps(monkeypatch, {"comp4": E.START_MIN_AGE_H["comp4"] - 1})
    assert m.state == "HEALTHY"


def test_episode_starts_at_exactly_the_floor_age(monkeypatch):
    m = _healthy_machine_after_steps(monkeypatch, {"comp4": E.START_MIN_AGE_H["comp4"]}, steps=1)
    assert m.state == "DEGRADING" and m.signal == "vibration"


def test_only_the_degrading_signals_component_age_matters(monkeypatch):
    """다른 부품이 어려도(comp1) 열화할 부품(comp4)이 오래됐으면 시작한다."""
    m = _healthy_machine_after_steps(monkeypatch, {"comp1": 10.0, "comp4": 5000.0}, steps=1)
    assert m.state == "DEGRADING"


def test_without_ages_episode_start_is_unchanged(monkeypatch):
    """comp_ages를 안 주면 예전과 같다(기존 호출·테스트 호환)."""
    m = _healthy_machine_after_steps(monkeypatch, None, steps=1)
    assert m.state == "DEGRADING"


def _tick_with_ages(seed_db, monkeypatch, age_hours):
    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 1.0)
    monkeypatch.setattr(E, "draw_initial_ages", lambda rng: {c: age_hours for c in E.COMPONENTS})
    sim_loop._tick_once()
    return sim_store.load_states()


def test_tick_passes_component_ages_so_young_components_do_not_degrade(seed_db, monkeypatch):
    """게이트를 엔진에 만들어도 틱이 나이를 안 넘기면 소용없다 - 라이브 배선까지 확인."""
    states = _tick_with_ages(seed_db, monkeypatch, age_hours=100.0)
    assert all(m.state == "HEALTHY" for m in states.values())


def test_tick_lets_old_components_degrade(seed_db, monkeypatch):
    states = _tick_with_ages(seed_db, monkeypatch, age_hours=5000.0)
    assert all(m.state != "HEALTHY" for m in states.values())


def test_seed_makes_a_degrading_machines_component_old_enough(seed_db, monkeypatch):
    """이미 열화 중인(주입된) 설비의 부품은 어리게 심으면 안 된다 - 주입 후 시작하는 데모 흐름."""
    import sqlite3

    from data import sim_loop

    states = sim_store.load_states()
    states[5].state, states[5].signal = "DEGRADING", "volt"
    for seed in range(300):
        monkeypatch.setattr(sim_loop, "SIM_SEED", seed)
        conn = sqlite3.connect(seed_db)
        conn.execute("DELETE FROM sim_maint")
        import pandas as pd
        sim_loop._seed_maintenance_history(conn, pd.Timestamp("2026-10-01 00:00:00"), [5], states)
        row = conn.execute('SELECT datetime FROM sim_maint WHERE "machineID"=5 AND comp=\'comp1\'').fetchone()
        conn.close()
        age = (pd.Timestamp("2026-10-01 00:00:00") - pd.Timestamp(row[0])).total_seconds() / 3600
        assert age >= E.START_MIN_AGE_H["comp1"] - 1, (seed, age)  # floor("h") 오차 1시간


def test_inject_ages_a_young_component_so_the_model_can_see_the_degradation(seed_db, monkeypatch):
    import sqlite3

    import pandas as pd

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)  # inject가 '지금' 시각을 이 DB의 sim_telemetry에서 읽는다

    now = pd.Timestamp("2026-10-01 00:00:00")
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES (?, 7, 1, 1, 1, 1)", (str(now),))
    conn.execute("INSERT INTO sim_maint VALUES (?, 7, 'comp4')", (str(now - pd.Timedelta(hours=100)),))
    conn.execute("INSERT INTO sim_maint VALUES (?, 7, 'comp1')", (str(now - pd.Timedelta(hours=100)),))
    conn.commit()
    conn.close()

    sim_loop.inject(7, "vibration")

    conn = sqlite3.connect(seed_db)
    rows = dict((c, d) for d, c in conn.execute('SELECT datetime, comp FROM sim_maint WHERE "machineID"=7'))
    conn.close()
    assert (now - pd.Timestamp(rows["comp4"])).total_seconds() / 3600 >= E.START_MIN_AGE_H["comp4"] - 1
    assert pd.Timestamp(rows["comp1"]) == now - pd.Timedelta(hours=100)  # 다른 부품은 그대로


def test_a_failure_resets_the_component_age_within_the_same_tick(seed_db, monkeypatch):
    """고장 직후 같은 틱 안에서 바로 같은 부품이 다시 열화를 시작하면 안 된다 - 틱이 시작할 때 읽어 둔
    나이를 고장 처리 후에도 그대로 쓰면(교체한 부품이 노후한 것으로 보임) 이 게이트가 뚫린다.
    #3은 comp4만 오래됐고(나머지 부품은 어림) 06시에 comp4가 고장난다 -> 07시에 다시 시작할 수 있는
    부품은 (낡은 나이를 잘못 쓰는 경우의) comp4뿐이다."""
    import sqlite3

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 2)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 1.0)

    class _AlwaysVibration(random.Random):
        """열화할 신호를 항상 vibration(comp4)으로 뽑는다 - 무작위로 다른 부품이 뽑혀 게이트에 걸리면
        나이를 잘못 쓰는 버그가 있어도 우연히 통과한다."""

        def choice(self, seq):
            return "vibration" if "vibration" in seq else super().choice(seq)

    monkeypatch.setattr(sim_loop, "_load_rng", lambda: _AlwaysVibration(0))
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-01 05:00:00', 1, 1, 1, 1, 1)")  # 틱의 기준 시각
    for comp, age_h in (("comp1", 100), ("comp2", 100), ("comp3", 100), ("comp4", 5000)):
        ts = (pd.Timestamp("2026-10-01 05:00:00") - pd.Timedelta(hours=age_h))
        conn.execute("INSERT INTO sim_maint VALUES (?, 3, ?)", (str(ts), comp))
    conn.commit()
    conn.close()
    states = sim_store.load_states()
    states[3].state, states[3].signal, states[3].direction = "DEGRADING", "vibration", 1
    states[3].drift_sigma, states[3].lead_hours, states[3].elapsed = 1.5, 1, 0
    sim_store.save_states(states)

    sim_loop._tick_once()

    conn = sqlite3.connect(seed_db)
    failed = conn.execute('SELECT datetime FROM sim_failures WHERE "machineID"=3').fetchall()
    conn.close()
    assert failed == [("2026-10-01 06:00:00",)]
    assert sim_store.load_states()[3].state == "HEALTHY"


# ---- 2026-10-01: 정기 정비와 고장률 보정 ---------------------------------------------------------------
# 열화 시작이 부품 나이(START_MIN_AGE_H)에 의존하게 되면서, 정기 정비로 나이가 되돌아가지 않으면 시뮬레이션이 길어질수록
# 모든 부품이 열화 가능한 나이가 돼서 고장률이 계속 오른다. 실제 정비 기록의 정기 정비는 부품당 연 5.36회(2015년).
def _memory_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE TABLE sim_maint ("datetime" TIMESTAMP, "machineID" INTEGER, "comp" TEXT)')
    return conn


def test_preventive_maintenance_rate_matches_the_measured_rate_and_skips_unhealthy_machines(monkeypatch):
    from data import sim_loop

    states = {i: E.MachineSim(i) for i in range(1, 101)}
    for i in range(51, 101):
        states[i].state = "DEGRADING"
    conn = _memory_conn()
    last: dict = {}
    base = pd.Timestamp("2026-10-01")
    hours = 3000
    for h in range(hours):
        sim_loop._preventive_maintenance(conn, base + pd.Timedelta(hours=h), states, last)
    events = conn.execute('SELECT "machineID", datetime, COUNT(*) FROM sim_maint GROUP BY 1, 2').fetchall()

    assert all(m <= 50 for m, _, _ in events)  # 열화 중인 설비는 정비하지 않는다
    rate = len(events) / (50 * hours)  # 설비·시간당 사건 수 (실제: 연 14.61회 = 0.001668)
    assert abs(rate - 0.001668) / 0.001668 < 0.15, rate
    two = sum(1 for _, _, k in events if k == 2) / len(events)  # 부품 2개를 함께 정비하는 사건의 비율(실제 28%)
    assert abs(two - 0.28) < 0.07, two
    assert len(last) > 0 and all(machine <= 50 for machine, _ in last)  # 틱이 쓰는 나이 표도 갱신된다


def test_failure_event_services_a_second_component_at_the_measured_share():
    """실제 고장 사건의 46%(320/702)가 고장난 부품 말고 다른 부품 하나도 같이 정비한다 - 같이 정비되는 부품은
    고장난 부품이 아니다."""
    from data import sim_loop

    base = pd.Timestamp("2026-10-01")
    picks = [sim_loop._failure_companion(m, base + pd.Timedelta(hours=h), "comp2") for m in range(1, 101) for h in range(40)]

    share = sum(1 for p in picks if p) / len(picks)
    assert abs(share - 0.456) < 0.025, share
    assert "comp2" not in picks
    assert {p for p in picks if p} == {"comp1", "comp3", "comp4"}  # 다른 부품이 골고루


def test_tick_services_the_companion_component_when_a_machine_fails(seed_db, monkeypatch):
    """고장 사건에서 두 번째 부품 정비가 실제로 기록되는지(배선)."""
    import sqlite3

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 0.0)
    monkeypatch.setattr(E, "P_PM_EVENT", 0.0)
    monkeypatch.setattr(E, "FAILURE_EXTRA_COMP_PROB", 1.0)
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-01 05:00:00', 1, 1, 1, 1, 1)")
    conn.commit()
    conn.close()
    states = sim_store.load_states()
    states[3].state, states[3].signal, states[3].direction = "DEGRADING", "vibration", 1
    states[3].drift_sigma, states[3].lead_hours, states[3].elapsed = 1.5, 1, 0
    sim_store.save_states(states)

    sim_loop._tick_once()

    conn = sqlite3.connect(seed_db)
    comps = sorted(c for (c,) in conn.execute("SELECT comp FROM sim_maint WHERE \"machineID\"=3 AND datetime='2026-10-01 06:00:00'"))
    conn.close()
    assert len(comps) == 2 and "comp4" in comps  # 고장난 comp4 + 다른 부품 하나


def test_preventive_maintenance_is_reproducible_per_seed_and_hour(monkeypatch):
    from data import sim_loop

    monkeypatch.setattr(E, "P_PM_EVENT", 0.05)
    states = {i: E.MachineSim(i) for i in range(1, 101)}
    ts = pd.Timestamp("2026-10-01 12:00:00")

    def once(seed):
        monkeypatch.setattr(sim_loop, "SIM_SEED", seed)
        conn = _memory_conn()
        sim_loop._preventive_maintenance(conn, ts, states, {})
        return conn.execute('SELECT "machineID", comp FROM sim_maint ORDER BY 1, 2').fetchall()

    assert once(42) == once(42)
    assert once(42) != once(43)


def test_tick_runs_preventive_maintenance(seed_db, monkeypatch):
    """함수가 있어도 틱이 부르지 않으면 정비가 없는 라이브로 돌아간다."""
    import sqlite3

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 0.0)
    monkeypatch.setattr(E, "P_PM_EVENT", 1.0)

    sim_loop._tick_once()

    conn = sqlite3.connect(seed_db)
    n = conn.execute('SELECT COUNT(*) FROM sim_maint WHERE datetime = (SELECT MAX(datetime) FROM sim_telemetry)').fetchone()[0]
    conn.close()
    assert 100 <= n <= 200  # 확률 1이면 이번 틱의 시각에 100대 모두 정비 사건(부품 1~2개씩)


def test_yearly_failure_rate_stays_near_the_measured_rate_with_age_gating_and_maintenance(monkeypatch):
    """P_EPISODE를 부품 나이 게이트와 정기 정비를 거친 뒤의 고장률로 보정했다 - 설비당 연 7.6회(실제)에서
    크게 벗어나면 보정이 깨진 것이다. (게이트나 정기 정비를 끄면 각각 아래/위로 벗어난다.)"""
    import random
    import sqlite3

    from data import sim_loop

    monkeypatch.setattr(sim_loop, "SIM_SEED", 1)
    rng = random.Random(1)
    states = {i: E.MachineSim(i) for i in range(1, 101)}
    t0 = pd.Timestamp("2026-10-01")
    last = {}
    for i in states:
        for c, a in sim_loop._draw_machine_ages(f"1:maint:{i}").items():
            last[(i, c)] = t0 - pd.Timedelta(hours=a)
    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE TABLE sim_maint ("datetime" TIMESTAMP, "machineID" INTEGER, "comp" TEXT)')
    failures = 0
    for h in range(1, 8760 + 1):
        ts = t0 + pd.Timedelta(hours=h)
        sim_loop._preventive_maintenance(conn, ts, states, last)
        for m in states.values():
            ages = sim_loop._comp_ages(last, m.machine_id, ts) if m.state == "HEALTHY" else None
            r = E.step(m, rng, ts.hour, comp_ages=ages)
            if r["failure"]:
                failures += 1
                last[(m.machine_id, r["maint"])] = ts
                companion = sim_loop._failure_companion(m.machine_id, ts, r["maint"])
                if companion:
                    last[(m.machine_id, companion)] = ts
    per_machine = failures / 100
    assert 6.8 <= per_machine <= 8.3, per_machine


# 고장 직전 정비 후 경과시간의 하한(실측, 1~10% 지점): 이 나이 아래에서는 실제로 거의 고장나지 않는다.
# 기대값은 코드 상수와 별개로 여기에 다시 적었다 - comp2만 720시간부터이고 나머지는 1080시간부터다.
EXPECTED_START_FLOOR = {"vibration": ("comp4", 1080), "volt": ("comp1", 1080), "pressure": ("comp3", 1080), "rotate": ("comp2", 720)}


@pytest.mark.parametrize("signal", sorted(EXPECTED_START_FLOOR))
def test_each_component_has_its_own_measured_start_floor(monkeypatch, signal):
    comp, floor = EXPECTED_START_FLOOR[signal]
    monkeypatch.setattr(E, "P_EPISODE", 1.0)
    monkeypatch.setattr(E, "SIGNALS", [signal])

    young = E.MachineSim(1)
    old = E.MachineSim(1)
    E.step(young, random.Random(0), 10, comp_ages={comp: floor - 1})
    E.step(old, random.Random(0), 10, comp_ages={comp: floor})

    assert young.state == "HEALTHY" and old.state == "DEGRADING"


# ---- 2026-10-01 test-engineer: 변이 테스트에서 살아남은 경로 보강 ----------------------------------------
# 아래 테스트는 각각 적힌 변이를 코드에 넣었을 때 기존 스위트가 통과하던 구멍을 막는다.

def test_silence_is_decided_per_episode_at_the_measured_rate_per_component():
    """조용한 고장 여부는 에피소드마다 정해진다 - 설비 번호만으로 정하면 어떤 설비는 매번 조용하고 나머지는
    절대 조용하지 않게 된다(설비 100대로 보는 비율 테스트는 그래도 통과했다). 한 설비의 에피소드 2만 건을
    _is_silent에 직접 물어 부품별 실측 비율과 비교한다 - 시뮬레이션 없이 빨라서 오차 범위를 좁게 잡을 수 있다
    (comp4 0.017을 두 배로 바꾸는 변이도 위의 6년 시뮬레이션 테스트(±0.025)로는 안 잡혔다)."""
    n = 20000
    for comp, expected in EXPECTED_SILENT.items():
        silent = sum(
            E._is_silent(E.MachineSim(1, drift_sigma=0.8 + i * 1e-4, lead_hours=44 + i % 9), comp) for i in range(n)
        )
        # 이항분포 표준오차 약 0.0009~0.0015 - 0.006이면 4시그마 이상이고 상수를 절반/두 배로 바꾸면 벗어난다
        assert abs(silent / n - expected) < 0.006, (comp, silent / n)


def test_preventive_maintenance_draws_do_not_depend_on_other_machines_states(monkeypatch):
    """정기 정비의 난수는 설비 상태와 무관하게 설비마다 같은 개수를 쓴다(docstring의 약속) - 정상일 때만 뽑으면
    한 설비가 열화를 시작하는 순간 뒤 번호 설비들의 정비 결과가 전부 바뀐다(같은 seed의 재현성이 상태에 묶인다)."""
    from data import sim_loop

    monkeypatch.setattr(E, "P_PM_EVENT", 0.3)  # 사건이 자주 나야 '사건 났을 때만 부품을 뽑는' 변이도 드러난다

    def events(degrading):
        states = {i: E.MachineSim(i, state="DEGRADING" if i in degrading else "HEALTHY") for i in range(1, 101)}
        conn = _memory_conn()
        for h in range(20):
            sim_loop._preventive_maintenance(conn, pd.Timestamp("2026-10-01") + pd.Timedelta(hours=h), states, {})
        return set(conn.execute('SELECT datetime, "machineID", comp FROM sim_maint WHERE "machineID" > 50'))

    assert events(set()) == events(set(range(1, 51)))  # 51~100번은 둘 다 정상 - 앞 번호 상태가 결과를 바꾸면 안 된다


def test_last_maintenance_is_the_newest_record_per_component():
    """틱이 매번 DB에서 다시 읽는 '마지막 정비' - 가장 오래된 기록을 쓰면 심어 둔 정비 이력이 영원히 마지막
    정비가 되어 부품 나이 게이트가 시간이 갈수록 무력해진다(고장률이 계속 오른다)."""
    from data import sim_loop

    conn = _memory_conn()
    conn.execute("INSERT INTO sim_maint VALUES ('2026-07-01 00:00:00', 1, 'comp1')")
    conn.execute("INSERT INTO sim_maint VALUES ('2026-09-30 06:00:00', 1, 'comp1')")
    conn.execute("INSERT INTO sim_maint VALUES ('2026-08-01 00:00:00', 1, 'comp1')")
    assert sim_loop._load_last_maint(conn) == {(1, "comp1"): pd.Timestamp("2026-09-30 06:00:00")}


def test_preventive_maintenance_runs_every_hour_of_a_multi_hour_tick(seed_db, monkeypatch):
    """데모 설정은 SIM_HOURS_PER_TICK=12(.env.example)다 - 틱의 첫 시간에만 정비하면 정기 정비율이 12분의 1이
    되어 부품이 늙고 고장률이 실제보다 올라간다. 확률 1이면 틱 안의 매 시각마다 100대 모두 정비 사건이 있다."""
    import sqlite3

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 3)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 0.0)
    monkeypatch.setattr(E, "P_PM_EVENT", 1.0)
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-01 05:00:00', 1, 1, 1, 1, 1)")
    conn.commit()
    conn.close()

    sim_loop._tick_once()

    conn = sqlite3.connect(seed_db)
    for ts in ("2026-10-01 06:00:00", "2026-10-01 07:00:00", "2026-10-01 08:00:00"):
        machines = conn.execute('SELECT COUNT(DISTINCT "machineID") FROM sim_maint WHERE datetime=?', (ts,)).fetchone()[0]
        assert machines == 100, (ts, machines)
    conn.close()


def test_companion_component_is_young_again_within_the_same_tick(seed_db, monkeypatch):
    """고장 사건에서 같이 정비한 부품도 같은 틱 안에서 바로 나이가 0이 돼야 한다 - DB에만 쓰고 틱이 쓰는 나이 표를
    안 고치면, 12시간 틱 안에서 방금 정비한 부품이 노후한 것으로 보여 곧바로 열화를 시작할 수 있다.
    #3은 모든 부품이 오래됐고(5000시간) 06시에 comp4가 고장나며 같이 정비하는 부품이 반드시 생긴다 -> 07시에
    그 같이 정비한 부품의 신호로만 열화를 시도하게 하면, 게이트에 걸려 정상으로 남아야 한다."""
    import sqlite3

    from data import sim_loop, sim_query

    t06 = pd.Timestamp("2026-10-01 06:00:00")
    monkeypatch.setattr(E, "FAILURE_EXTRA_COMP_PROB", 1.0)
    companion = sim_loop._failure_companion(3, t06, "comp4")
    companion_signal = {"comp1": "volt", "comp2": "rotate", "comp3": "pressure"}[companion]  # 코드 표와 별개로 적음

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 2)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 1.0)
    monkeypatch.setattr(E, "P_PM_EVENT", 0.0)

    class _AlwaysCompanion(random.Random):
        def choice(self, seq):
            return companion_signal if companion_signal in seq else super().choice(seq)

    monkeypatch.setattr(sim_loop, "_load_rng", lambda: _AlwaysCompanion(0))
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-01 05:00:00', 1, 1, 1, 1, 1)")
    for comp in E.COMPONENTS:
        conn.execute("INSERT INTO sim_maint VALUES (?, 3, ?)", (str(t06 - pd.Timedelta(hours=5000)), comp))
    conn.commit()
    conn.close()
    states = sim_store.load_states()
    states[3].state, states[3].signal, states[3].direction = "DEGRADING", "vibration", 1
    states[3].drift_sigma, states[3].lead_hours, states[3].elapsed = 1.5, 1, 0
    sim_store.save_states(states)

    sim_loop._tick_once()

    conn = sqlite3.connect(seed_db)
    failed = conn.execute('SELECT datetime FROM sim_failures WHERE "machineID"=3').fetchall()
    conn.close()
    assert failed == [(str(t06),)]
    assert sim_store.load_states()[3].state == "HEALTHY"


def test_first_tick_seeds_an_injected_machines_component_old_enough(seed_db, monkeypatch):
    """데모 흐름 '시작 전에 주입 -> 시작': 첫 틱이 정비 이력을 심을 때 열화 중인 설비의 상태를 넘기지 않으면
    주입한 부품이 어리게 심겨서 모델이 경보를 내지 않는다(함수 단위 테스트는 states를 직접 넘겨서 이 배선을
    보지 못했다). 심는 나이를 전부 100시간으로 고정해 하한이 실제로 적용되는지만 본다."""
    import sqlite3

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100.0, 10.0) for s in E.SIGNALS})
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: ([], []))
    monkeypatch.setattr(E, "P_EPISODE", 0.0)
    monkeypatch.setattr(E, "P_PM_EVENT", 0.0)
    monkeypatch.setattr(E, "draw_initial_ages", lambda rng: {c: 100.0 for c in E.COMPONENTS})
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-01 05:00:00', 1, 1, 1, 1, 1)")
    conn.commit()
    conn.close()
    states = sim_store.load_states()
    states[5].state, states[5].signal, states[5].direction = "DEGRADING", "volt", 1
    states[5].drift_sigma, states[5].lead_hours = 4.5, 48
    sim_store.save_states(states)

    sim_loop._tick_once()

    conn = sqlite3.connect(seed_db)
    (seeded,) = conn.execute('SELECT datetime FROM sim_maint WHERE "machineID"=5 AND comp=\'comp1\'').fetchone()
    conn.close()
    age = (pd.Timestamp("2026-10-01 05:00:00") - pd.Timestamp(seeded)).total_seconds() / 3600
    assert age >= 1080 - 1, age  # comp1 하한 1080시간(코드 상수와 별개로 적음), floor("h") 오차 1시간


def test_inject_ages_every_young_maintenance_not_just_the_newest(seed_db, monkeypatch):
    """하한보다 어린 정비가 둘 이상이면 가장 최근 한 건만 옮겨서는 다음 기록이 새로 마지막 정비가 돼 부품이 여전히
    어리다 - 모델은 마지막 정비를 쓴다(test-engineer가 임시 DB로 재현: 10h와 300h 두 건 -> 주입 후 300h)."""
    import sqlite3

    from data import sim_loop, sim_query

    monkeypatch.setattr(sim_query, "DB_PATH", seed_db)
    now = pd.Timestamp("2026-10-01 00:00:00")
    conn = sqlite3.connect(seed_db)
    conn.execute("INSERT INTO sim_telemetry VALUES (?, 7, 1, 1, 1, 1)", (str(now),))
    for hours in (10, 300):
        conn.execute("INSERT INTO sim_maint VALUES (?, 7, 'comp4')", (str(now - pd.Timedelta(hours=hours)),))
    conn.commit()
    conn.close()

    sim_loop.inject(7, "vibration")

    conn = sqlite3.connect(seed_db)
    last = sim_loop._load_last_maint(conn)[(7, "comp4")]
    conn.close()
    assert (now - last).total_seconds() / 3600 >= E.START_MIN_AGE_H["comp4"] - 1
