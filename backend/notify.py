"""
Slack 알림 - 결정론적 트리거(긴급/주의 신규 감지, 승인 결과)에서만 호출되는 얇은 webhook 클라이언트.
에이전트가 그때그때 판단해서 보내는 게 아니라 규칙이 고정된 알림이라 MCP 없이 직접 호출한다
(PHASE_7_PLAN.md Stage 1 참고). 외부 시스템(Slack) 장애가 본 기능(스캔/승인)을 절대 막으면 안 되므로
실패는 로그만 남기고 삼킨다.
"""

import os
import requests
from dotenv import load_dotenv


load_dotenv()
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")


def send_alert(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return

    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=5)
    except requests.exceptions.RequestException as e:
        print(f"[알림 실패] Slack webhook 호출 실패: {e}")
        