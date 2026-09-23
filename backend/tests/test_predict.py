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
