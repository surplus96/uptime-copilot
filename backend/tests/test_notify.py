"""회귀 테스트: 2026-09-18 세션에서 발견/수정된 두 가지 notify.py 버그.

버그 1 (security-reviewer 지적): 실패 로그에 예외 객체(str(e))를 그대로 찍어서,
Slack Webhook URL(그 자체가 비밀키) 전체가 콘솔에 노출됐다.
버그 2 (직접 테스트로 발견): raise_for_status()가 없어서, Slack이 4xx/5xx로
"정상 응답"하면 실패를 조용히 놓쳤다(예외가 안 나서 로그조차 안 남음).
"""
import pytest
import requests


class _FakeResponse:
    """requests.Response 흉내 - 실제 네트워크 없이 4xx 응답을 재현한다."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error: Not Found for url: "
                "https://hooks.slack.com/services/SECRET/PATH/HERE"
            )


def test_send_alert_catches_rejected_webhook_without_raising(monkeypatch):
    """버그 2 회귀: Slack이 404로 '정상 응답'해도 send_alert()는 예외를 밖으로
    던지면 안 된다(호출부인 scan_all_machines/finalize_node가 안 죽어야 하므로)."""
    import notify

    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET/PATH/HERE")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(404))

    notify.send_alert("테스트 메시지")  # 예외 없이 반환돼야 함(assert 대신 그냥 안 죽으면 통과)


def test_send_alert_never_logs_the_webhook_url(monkeypatch, capsys):
    """버그 1 회귀: 실패 로그 어디에도 원본 webhook URL(비밀키)이 찍히면 안 된다."""
    import notify

    secret_url = "https://hooks.slack.com/services/SECRET/PATH/HERE"
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", secret_url)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(404))

    notify.send_alert("테스트 메시지")

    printed = capsys.readouterr().out
    assert "SECRET" not in printed
    assert secret_url not in printed


def test_format_for_slack_bolds_labels_and_breaks_numbered_steps():
    """가독성 수정(2026-09-18): `[라벨]`은 굵게, 한 줄에 뭉친 `1. ... 2. ...`는
    줄바꿈된 목록으로 풀려야 Slack에서 스캔하기 쉽다."""
    import notify

    raw = "[부품] 베어링\n[조치사항] 1. 육안 검사 2. 교체"
    formatted = notify._format_for_slack(raw)

    assert "*[부품]*" in formatted
    assert "*[조치사항]*" in formatted
    assert "\n1. 육안 검사" in formatted
    assert "\n2. 교체" in formatted


def test_send_alert_noop_when_unconfigured(monkeypatch):
    """SLACK_WEBHOOK_URL 미설정 시 조용히 아무것도 안 해야 한다(요청 자체를 안 보냄)."""
    import notify

    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", None)
    called = False

    def _fail_if_called(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(requests, "post", _fail_if_called)
    notify.send_alert("테스트 메시지")

    assert called is False


@pytest.fixture
def isolated_audit(tmp_path, monkeypatch):
    """2026-09-29 CP-M2 교차 검토 지적 회귀: 예전엔 Slack 실제 전송의 성공/실패가
    감사 로그에 전혀 안 남았다(오프라인 차단만 기록됨) - 이제 실제로 기록되는지
    공유 DB가 아니라 격리된 DB로 확인한다."""
    from data import audit_log
    db_path = str(tmp_path / "test_audit.db")
    monkeypatch.setattr(audit_log, "DB_PATH", db_path)
    audit_log.init_audit_table()
    return audit_log


def test_send_alert_logs_success_to_audit(monkeypatch, isolated_audit):
    import notify

    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET/PATH/HERE")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(200))

    notify.send_alert("테스트 메시지", thread_id="t1")

    row = isolated_audit.list_events()[0]
    assert row["event_type"] == "external_push"
    assert row["result"] == "성공"
    assert row["thread_id"] == "t1"


def test_send_alert_logs_failure_to_audit(monkeypatch, isolated_audit):
    import notify

    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET/PATH/HERE")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(404))

    notify.send_alert("테스트 메시지", thread_id="t1")

    row = isolated_audit.list_events()[0]
    assert row["event_type"] == "external_push"
    assert row["result"] == "실패"


def test_send_alert_logs_blocked_by_offline_with_thread_id(monkeypatch, isolated_audit):
    """예전엔 blocked_by_offline 기록에 thread_id가 항상 NULL이라 어느 승인에서
    나온 차단인지 알 수 없었다 - 이제 호출부가 넘긴 thread_id가 그대로 남는다."""
    import notify
    from core import offline_guard

    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET/PATH/HERE")
    assert offline_guard.is_offline() is True

    notify.send_alert("테스트 메시지", thread_id="t1")

    row = isolated_audit.list_events()[0]
    assert row["event_type"] == "blocked_by_offline"
    assert row["result"] == "차단"
    assert row["thread_id"] == "t1"
