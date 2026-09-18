"""회귀 테스트: 2026-09-18 세션에서 발견/수정된 두 가지 notify.py 버그.

버그 1 (security-reviewer 지적): 실패 로그에 예외 객체(str(e))를 그대로 찍어서,
Slack Webhook URL(그 자체가 비밀키) 전체가 콘솔에 노출됐다.
버그 2 (직접 테스트로 발견): raise_for_status()가 없어서, Slack이 4xx/5xx로
"정상 응답"하면 실패를 조용히 놓쳤다(예외가 안 나서 로그조차 안 남음).
"""
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
