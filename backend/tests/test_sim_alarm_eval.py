"""ml/sim_alarm_eval.py(시뮬레이터 열화 -> 주의 경보 측정) 회귀 테스트.
실제 모델 파일은 CI에 없으므로 모델 호출(_risk_by_time)은 가짜로 바꾼다 - 여기서 확인하는 것은
측정 스크립트 자체의 계산(선행 시간, 스캔 간격 반영)과 임계값이 서비스와 같은 값인지다."""
import random

import pandas as pd

from agent import agent_service
from ml import sim_alarm_eval as ev

START = ev.START


def _episode(hours_with_features: int, failure_after_h: int) -> dict:
    return {
        "features": [object()] * hours_with_features,
        "times": [START + pd.Timedelta(hours=h) for h in range(hours_with_features)],
        "failure_ts": START + pd.Timedelta(hours=failure_after_h),
        "precursor_errors": 1,
    }


def test_threshold_is_the_same_one_the_service_uses():
    """서비스(agent_service)의 경보 기준과 어긋나면 측정 결과가 의미 없다."""
    assert ev.RISK_THRESHOLD == agent_service.RISK_THRESHOLD


def test_lead_hours_is_time_from_first_alarm_to_failure(monkeypatch):
    ep = _episode(hours_with_features=10, failure_after_h=10)
    monkeypatch.setattr(ev, "_risk_by_time", lambda e: [0.0, 0.0, 0.0, 0.7, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9])

    assert ev.lead_hours(ep) == 7.0  # 3시간째 첫 경보, 고장은 10시간째


def test_lead_hours_is_none_when_no_alarm_before_failure(monkeypatch):
    ep = _episode(hours_with_features=5, failure_after_h=5)
    monkeypatch.setattr(ev, "_risk_by_time", lambda e: [0.1, 0.2, 0.49, 0.3, 0.1])

    assert ev.lead_hours(ep) is None


def test_threshold_boundary_counts_as_alarm(monkeypatch):
    ep = _episode(hours_with_features=3, failure_after_h=3)
    monkeypatch.setattr(ev, "_risk_by_time", lambda e: [0.0, ev.RISK_THRESHOLD, 0.0])

    assert ev.lead_hours(ep) == 2.0  # 서비스는 `>=`로 경보를 낸다(agent_service.risk_alarm)


def test_tick_interval_skips_scans_between_ticks(monkeypatch):
    """스캔이 12시간마다만 돌면 그 사이에 뜬 경보는 다음 스캔 때에야 보인다."""
    ep = _episode(hours_with_features=30, failure_after_h=30)
    risks = [0.0] * 5 + [0.9] * 25  # 5시간째부터 경보
    monkeypatch.setattr(ev, "_risk_by_time", lambda e: risks)

    assert ev.lead_hours(ep, tick_hours=1) == 25.0
    assert ev.lead_hours(ep, tick_hours=12) == 18.0  # 12시간째 스캔에서 처음 본다


def test_lead_hours_none_when_episode_has_no_failure(monkeypatch):
    ep = _episode(hours_with_features=5, failure_after_h=5)
    ep["failure_ts"] = None
    monkeypatch.setattr(ev, "_risk_by_time", lambda e: [0.9] * 5)

    assert ev.lead_hours(ep) is None


def test_simulate_episode_reaches_failure_counts_errors_and_restores_signals(monkeypatch):
    """simulate_episode는 신호를 고정하려고 엔진의 전역 SIGNALS를 잠깐 바꾼다 - 되돌리지 않으면 같은
    프로세스의 다음 에피소드·다른 코드가 한 신호로만 열화한다. 전조 오류 수(precursor_errors)는
    '전조오류0건' 열의 근거라 서명 오류(rotate는 2개)가 실제로 세어져야 한다. DB 대신 고정 기준값."""
    monkeypatch.setattr(ev.sim_engine, "P_EPISODE", 1.0)
    monkeypatch.setattr(ev, "_baseline", lambda mid: {s: (100.0, 1.0) for s in ev.ALL_SIGNALS})
    monkeypatch.setattr(ev, "_original_maint", lambda mid: pd.DataFrame(columns=["datetime", "comp"]))
    monkeypatch.setattr(ev.pdm_operations, "get_machine_info", lambda mid: {"model": "model1", "age": 3})
    ep = ev.simulate_episode(5, "rotate", random.Random(0))

    assert ev.sim_engine.SIGNALS == ev.ALL_SIGNALS
    assert ep["failure_ts"] is not None and ep["failure_ts"].hour == ev.sim_engine.FAILURE_HOUR
    assert ep["precursor_errors"] >= 2
    assert len(ep["features"]) == len(ep["times"]) > 0 and all(t < ep["failure_ts"] for t in ep["times"])


def test_first_alarm_component_is_the_part_that_scored_highest_at_first_alarm(monkeypatch):
    """경보가 엉뚱한 부품을 가리키면 작업지시서가 다른 부품 블록을 만든다 - 처음 경보가 뜬 시각의 부품을 본다."""
    ep = _episode(hours_with_features=4, failure_after_h=4)
    monkeypatch.setattr(
        ev, "_risk_detail",
        lambda e: ([0.1, 0.8, 0.9, 0.9], ["comp1", "comp3", "comp2", "comp2"]),
    )

    assert ev.first_alarm_component(ep) == "comp3"  # 두 번째 시각이 첫 경보, 이후 comp2가 더 높아져도 첫 경보 기준


def test_first_alarm_component_is_none_without_alarm(monkeypatch):
    ep = _episode(hours_with_features=2, failure_after_h=2)
    monkeypatch.setattr(ev, "_risk_detail", lambda e: ([0.1, 0.2], ["comp1", "comp1"]))

    assert ev.first_alarm_component(ep) is None


def test_seeded_history_adds_one_past_maintenance_per_component():
    import random

    import pandas as pd

    base = pd.DataFrame({"datetime": [pd.Timestamp("2015-01-01")], "comp": ["comp1"]})

    out = ev._with_seeded_history(base, random.Random(1))

    assert len(out) == 1 + 4
    assert sorted(out["comp"].tolist()) == ["comp1", "comp1", "comp2", "comp3", "comp4"]
    assert (out["datetime"].iloc[1:] < ev.START).all()  # 전부 에피소드 시작 전


def test_seeded_history_keeps_the_degrading_component_above_the_start_floor():
    """엔진은 부품별 START_MIN_AGE_H보다 어린 부품에는 열화를 시작하지 않는다 - 측정도 같은 조건이어야 한다."""
    import random

    import pandas as pd

    from data import sim_engine

    base = pd.DataFrame({"datetime": pd.to_datetime([]), "comp": []})
    for seed in range(300):
        out = ev._with_seeded_history(base, random.Random(seed), "comp4")
        age = (ev.START - out[out["comp"] == "comp4"]["datetime"].iloc[0]).total_seconds() / 3600
        assert age >= sim_engine.START_MIN_AGE_H["comp4"], (seed, age)


def test_evaluate_restores_the_engines_episode_probability(monkeypatch):
    """evaluate는 열화를 첫 시간에 시작시키려고 엔진의 P_EPISODE를 1.0으로 바꾼다 - 되돌리지 않으면 같은
    프로세스에서 도는 시뮬레이터·다른 테스트가 매시간 모든 설비를 열화시킨다. 에피소드 본체는 가짜로 바꾸고
    (모델 파일 없음) 실행 중에는 1.0이었는지, 끝난 뒤에는 원래 값인지 본다."""
    from data import sim_engine

    before = sim_engine.P_EPISODE
    seen = []

    def fake_episode(machine_id, signal, rng, maint_hours, seed_maint):
        seen.append(sim_engine.P_EPISODE)
        return {"failure_ts": None}

    monkeypatch.setattr(ev, "simulate_episode", fake_episode)
    ev.evaluate(episodes=1, seed=0, tick_hours=1)

    assert seen and all(p == 1.0 for p in seen)
    assert sim_engine.P_EPISODE == before


def test_first_alarm_component_counts_the_threshold_itself_as_an_alarm(monkeypatch):
    """서비스는 `>=`로 경보를 낸다 - lead_hours와 같은 경계 규칙이어야 두 열(경보/부품 일치)이 같은 시각을 본다."""
    ep = _episode(hours_with_features=2, failure_after_h=2)
    monkeypatch.setattr(ev, "_risk_detail", lambda e: ([0.1, ev.RISK_THRESHOLD], ["comp1", "comp3"]))

    assert ev.first_alarm_component(ep) == "comp3"
