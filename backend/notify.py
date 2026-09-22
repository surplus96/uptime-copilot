"""
Slack 알림 - 결정론적 트리거(긴급/주의 신규 감지, 승인 결과)에서만 호출되는 얇은 webhook 클라이언트.
에이전트가 그때그때 판단해서 보내는 게 아니라 규칙이 고정된 알림이라 MCP 없이 직접 호출한다
(PHASE_7_PLAN.md Stage 1 참고). 외부 시스템(Slack) 장애가 본 기능(스캔/승인)을 절대 막으면 안 되므로
실패는 로그만 남기고 삼킨다.
"""

import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")


def _format_for_slack(text: str) -> str:
    """작업지시서 텍스트를 Slack mrkdwn으로 다듬는다 - `[라벨]`을 굵게 표시하고,
    한 줄로 나열된 번호 매김 조치사항(`1. ... 2. ...`)을 줄바꿈된 목록으로 풀어서,
    알림 채널에서 한눈에 스캔되게 한다."""
    text = re.sub(r"\[([^\]]+)\]", r"*[\1]*", text)
    text = re.sub(r"(?<=\s)(\d+\.\s)", r"\n\1", text)
    return text


def send_alert(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json={"text": _format_for_slack(text)}, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        # webhook URL 자체가 비밀키다 - requests의 예외 메시지는 요청 URL을 그대로
        # 포함하므로, str(e)를 절대 로그에 남기지 않는다(security-reviewer 지적, 2026-09-18).
        status = getattr(getattr(e, "response", None), "status_code", None)
        print(f"[알림 실패] Slack webhook 호출 실패: {type(e).__name__}" + (f" (status={status})" if status else ""))
        