import numpy as np
import pandas as pd

import ml.predict as predict


class _FakeModel:
    """실제 LightGBM 대신 쓰는 가짜 모델 - CI에는 학습된 모델 파일이 없으므로."""
    def predict_proba(self, X):
        return np.array([[0.1, 0.9]])

    def predict(self, X, pred_contrib=False):
        n = X.shape[1]
        contrib = np.zeros(n + 1)
        contrib[0], contrib[1], contrib[2] = 0.5, -0.3, 0.2  # 상위 3개가 이 순서로 나오게
        return np.array([contrib])


def test_predict_failure_risk_returns_top3_per_component(monkeypatch):
    feature_cols = ["volt_mean_3h", "error1_count_24h", "hours_since_maint_comp1", "model", "age"]
    fake_bundle = {"model": _FakeModel(), "feature_cols": feature_cols, "model_categories": ["model1", "model2"]}
    monkeypatch.setattr(predict, "_load_model_bundle", lambda comp: fake_bundle)

    fake_row = pd.DataFrame([{
        "volt_mean_3h": 170.0, "error1_count_24h": 0, "hours_since_maint_comp1": 100.0,
        "model": "model1", "age": 5,
    }])
    monkeypatch.setattr(predict, "_build_live_feature_row", lambda machine_id: fake_row)

    result = predict.predict_failure_risk(1)

    assert set(result.keys()) == {"comp1", "comp2", "comp3", "comp4"}
    for comp_result in result.values():
        assert comp_result["probability"] == 0.9
        assert len(comp_result["top_features"]) == 3
        assert comp_result["top_features"][0]["feature"] == "volt_mean_3h"


def test_build_feature_row_matches_training_window_definitions():
    """라이브 피처 정의가 학습(ml/build_features.py)과 같아야 모델 점수가 의미 있다 - 오류 개수는
    (T-w, T] 구간(경계: w시간 전 정각은 빼고 T 정각은 넣는다), 정비 경과시간은 부품별 '가장 최근'
    정비 기준이고 기록이 없으면 NaN(학습 데이터도 NaN), 3h 평균은 마지막 3개 행만 본다.
    build_feature_row는 DB 없이 부를 수 있어 경계값을 직접 넣어 확인한다."""
    now = pd.Timestamp("2026-10-03 06:00:00")
    times = [now - pd.Timedelta(hours=h) for h in range(29, -1, -1)]
    tele = pd.DataFrame({"datetime": times})
    for sig in predict.SIGNALS:
        tele[sig] = [100.0] * 27 + [200.0] * 3  # 마지막 3시간만 다르다
    err = pd.DataFrame([
        (now, "error1"),                              # T 정각 - 24h·48h 모두 포함
        (now - pd.Timedelta(hours=24), "error1"),     # 24h 경계 - 24h 제외, 48h 포함
        (now - pd.Timedelta(hours=48), "error1"),     # 48h 경계 - 둘 다 제외
        (now + pd.Timedelta(hours=1), "error1"),      # 미래 - 둘 다 제외
        (now - pd.Timedelta(hours=1), "error2"),
    ], columns=["datetime", "errorID"])
    maint = pd.DataFrame([
        (now - pd.Timedelta(hours=500), "comp1"),
        (now - pd.Timedelta(hours=100), "comp1"),     # 가장 최근 것이 기준
    ], columns=["datetime", "comp"])

    row = predict.build_feature_row(now, tele, err, maint, {"model": "model3", "age": 7}).iloc[0]

    assert row["error1_count_24h"] == 1 and row["error1_count_48h"] == 2
    assert row["error2_count_24h"] == 1 and row["error3_count_48h"] == 0
    assert row["volt_mean_3h"] == 200.0 and row["volt_mean_24h"] == (21 * 100.0 + 3 * 200.0) / 24
    assert row["hours_since_maint_comp1"] == 100.0
    assert np.isnan(row["hours_since_maint_comp2"])  # comp1 정비가 다른 부품에 새면 안 된다
    assert (row["model"], row["age"]) == ("model3", 7)


def test_missing_maintenance_record_is_logged_because_the_model_treats_it_as_just_maintained(monkeypatch, caplog):
    """학습 데이터에 이 피처의 결측이 한 건도 없어서 NaN이 0(방금 정비함)처럼 다뤄진다 - 실제 고장 재생에서 경보율이
    99.2% -> 43%. 점수가 조용히 낮아지므로 로그로 알려야 한다."""
    import logging

    feature_cols = ["volt_mean_3h", "hours_since_maint_comp1", "model", "age"]
    fake_bundle = {"model": _FakeModel(), "feature_cols": feature_cols, "model_categories": ["model1"]}
    monkeypatch.setattr(predict, "_load_model_bundle", lambda comp: fake_bundle)
    row = pd.DataFrame([{
        "volt_mean_3h": 170.0, "model": "model1", "age": 5,
        "hours_since_maint_comp1": 100.0, "hours_since_maint_comp2": np.nan,
        "hours_since_maint_comp3": 50.0, "hours_since_maint_comp4": np.nan,
    }])
    monkeypatch.setattr(predict, "_build_live_feature_row", lambda machine_id: row)

    with caplog.at_level(logging.WARNING, logger=predict.logger.name):
        predict.predict_failure_risk(7)

    assert "설비 #7" in caplog.text and "comp2, comp4" in caplog.text and "comp1" not in caplog.text.split("정비 기록이 없어")[0]


def test_no_warning_when_every_component_has_a_maintenance_record(monkeypatch, caplog):
    import logging

    feature_cols = ["volt_mean_3h", "model", "age"]
    fake_bundle = {"model": _FakeModel(), "feature_cols": feature_cols, "model_categories": ["model1"]}
    monkeypatch.setattr(predict, "_load_model_bundle", lambda comp: fake_bundle)
    row = pd.DataFrame([{"volt_mean_3h": 170.0, "model": "model1", "age": 5, **{f"hours_since_maint_comp{i}": 100.0 for i in range(1, 5)}}])
    monkeypatch.setattr(predict, "_build_live_feature_row", lambda machine_id: row)

    with caplog.at_level(logging.WARNING, logger=predict.logger.name):
        predict.predict_failure_risk(7)

    assert "정비 기록이 없어" not in caplog.text
