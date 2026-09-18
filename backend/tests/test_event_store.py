"""회귀 테스트: 2026-09-17~18 세션에서 발견/수정된 evidence_at 억제 로직 버그.

원래 버그: complete_events()가 evidence_at 대신 _dataset_now()(완료 처리 시각)를
completed_events에 저장했다. 그 결과 긴급(고정된 과거 근거)은 영원히 억제되고,
주의(매 틱 갱신되는 근거)는 새 근거 없이도 매 틱 되살아나는 비대칭이 생겼다.
지금은 evidence_at을 그대로 이어받아야 한다.
"""


def test_complete_events_carries_over_evidence_at_not_wallclock(event_store_module):
    event_store_module.save_event(1, "긴급", "설비 #1 진단", "2016-01-01 08:00:00")

    completed = event_store_module.complete_events([1])

    assert completed == 1
    completed_map = event_store_module.get_completed_evidence_map()
    # 완료 처리 시각(telemetry 최신 시각 = 09:00:00)이 아니라, 그 이벤트의 실제 근거
    # 시각(08:00:00)이 그대로 저장돼야 한다.
    assert completed_map[1] == "2016-01-01 08:00:00"


def test_completed_evidence_map_only_covers_completed_machines(event_store_module):
    event_store_module.save_event(2, "주의", "설비 #2 진단", "2016-01-01 06:00:00")
    # 완료 처리 안 함 - completed_events는 비어있어야 한다.

    assert event_store_module.get_completed_evidence_map() == {}


def test_detected_evidence_map_reflects_currently_saved_state(event_store_module):
    """회귀 대상: 알림 스팸 - scan_all_machines()는 이 맵과 방금 진단한 evidence_at을
    비교해서 '이미 같은 근거로 알린 적 있는지' 판단한다. save_event가 이 맵에
    정확히 반영돼야 그 비교가 의미 있다."""
    event_store_module.save_event(5, "주의", "설비 #5 진단", "2016-01-01 06:00:00")

    assert event_store_module.get_detected_evidence_map() == {5: "2016-01-01 06:00:00"}

    # 같은 근거로 재저장(재스캔 시뮬레이션) - 값이 그대로 유지되는지
    event_store_module.save_event(5, "주의", "설비 #5 진단", "2016-01-01 06:00:00")
    assert event_store_module.get_detected_evidence_map()[5] == "2016-01-01 06:00:00"

    # 근거가 실제로 바뀌면(새 이상 감지) 맵도 갱신돼야 한다 - 이때는 알림이 다시 나가야
    # 정상이므로, is_genuinely_new = (old != new) 비교가 True가 되는 게 맞다.
    event_store_module.save_event(5, "주의", "설비 #5 진단", "2016-01-01 09:00:00")
    assert event_store_module.get_detected_evidence_map()[5] == "2016-01-01 09:00:00"


def test_delete_events_does_not_touch_completed_evidence(event_store_module):
    """삭제는 completed_events에 아무 흔적도 안 남겨야 한다 - '기록 없이 삭제'라는
    설계 의도와, 완료 처리와 삭제를 혼동하지 않는지 확인."""
    event_store_module.save_event(9, "긴급", "설비 #9 진단", "2015-12-31 06:00:00")

    deleted = event_store_module.delete_events([9])

    assert deleted == 1
    assert event_store_module.get_completed_evidence_map() == {}
    assert event_store_module.get_detected_evidence_map() == {}
