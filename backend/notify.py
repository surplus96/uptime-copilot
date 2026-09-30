"""
Slack 알림 - 결정론적 트리거(긴급/주의 신규 감지, 승인 결과)에서만 호출되는 얇은 webhook 클라이언트.
에이전트가 그때그때 판단해서 보내는 게 아니라 규칙이 고정된 알림이라 MCP 없이 직접 호출한다
(docs/design/PHASE_7_PLAN.md Stage 1 참고). 외부 시스템(Slack) 장애가 본 기능(스캔/승인)을 절대 막으면 안 되므로
실패는 로그만 남기고 삼킨다.
"""

import logging
import os
import re

import requests
from dotenv import load_dotenv

from core import offline_guard
from data import audit_log

load_dotenv()
logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")


def _format_for_slack(text: str) -> str:
    """작업지시서 텍스트를 Slack mrkdwn으로 다듬는다 - `[라벨]`을 굵게 표시하고,
    한 줄로 나열된 번호 매김 조치사항(`1. ... 2. ...`)을 줄바꿈된 목록으로 풀어서,
    알림 채널에서 한눈에 스캔되게 한다."""
    text = re.sub(r"\[([^\]]+)\]", r"*[\1]*", text)
    text = re.sub(r"(?<=\s)(\d+\.\s)", r"\n\1", text)
    return text


def send_alert(text: str, *, thread_id: str | None = None) -> None:
    """2026-09-29 CP-M2 교차 검토 지적: 오프라인 차단만 감사에 남고 실제 전송의
    성공/실패는 전혀 기록되지 않았다 - 이제 모든 결과 분기를 기록한다. thread_id는
    선택값(호출부 대부분은 특정 승인 건과 무관한 배치 스캔이라 없음 - finalize_node만
    실제 값을 넘긴다)."""
    if offline_guard.is_offline():
        audit_log.log_event("blocked_by_offline", thread_id=thread_id, target="Slack",
                             summary="오프라인 - 웹훅 스킵", result="차단")
        return
    if not SLACK_WEBHOOK_URL:
        # M5: 미설정 스킵도 하나의 결과다 - 이 행이 없으면 감사 로그만 보고는
        # "전송 안 됨"과 "기록 누락"을 구분할 수 없다(CMMS의 skipped_unconfigured와 동일).
        audit_log.log_event("external_push", thread_id=thread_id, target="Slack",
                             summary="Slack 알림 전송", result="skipped_unconfigured")
        return

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json={"text": _format_for_slack(text)}, timeout=5)
        response.raise_for_status()
        audit_log.log_event("external_push", thread_id=thread_id, target="Slack",
                             summary="Slack 알림 전송", result="성공")
    except requests.exceptions.RequestException as e:
        # webhook URL 자체가 비밀키다 - requests의 예외 메시지는 요청 URL을 그대로
        # 포함하므로, str(e)를 절대 로그에 남기지 않는다(security-reviewer 지적, 2026-09-18).
        status = getattr(getattr(e, "response", None), "status_code", None)
        logger.error(f"[알림 실패] Slack webhook 호출 실패: {type(e).__name__}" + (f" (status={status})" if status else ""))
        audit_log.log_event("external_push", thread_id=thread_id, target="Slack",
                             summary="Slack 알림 전송", result="실패")
