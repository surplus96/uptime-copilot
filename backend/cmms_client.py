"""
Atlas CMMS 작업지시서 push - 결정론적 트리거(긴급 승인)에서만 호출된다.
Stage 1(notify.py)과 같은 원칙: 이미 승인이 끝난 뒤의 고정된 행동이라 에이전트가 도구를
고를 필요가 없다. 그래서 langchain-mcp-adapters(LLM에게 도구 선택을 맡기는 프레임워크) 대신
mcp SDK로 정해진 도구 하나를 직접, 결정론적으로 호출한다 (docs/design/PHASE_7_PLAN.md Stage 2 참고).
CMMS 장애가 승인 흐름을 막으면 안 되므로 예외는 호출부(finalize_node)에서 삼킨다.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from core import offline_guard
from data import cmms_delivery

load_dotenv()
logger = logging.getLogger(__name__)
CMMS_MCP_URL = os.getenv("CMMS_MCP_URL")
CMMS_MCP_TOKEN = os.getenv("CMMS_MCP_TOKEN")


# 프로토타입 단계 - 설비 번호 -> Atlas assetId 매핑이 지금은 테스트 자산 하나뿐이다.
# 실제 벤더 연동 시 Atlas의 자산 규칙에 맞춰 조회하거나 매핑 테이블로 교체해야 한다.
MACHINE_ID_TO_ASSET_ID = {84: 1}
if os.getenv("CMMS_ASSET_MAP"):
    configured = json.loads(os.environ["CMMS_ASSET_MAP"])
    if not isinstance(configured, dict) or any(
        not str(machine).isdigit() or int(machine) <= 0 or type(asset) is not int or asset <= 0
        for machine, asset in configured.items()
    ):
        raise ValueError("CMMS_ASSET_MAP은 양의 설비 번호와 Atlas asset ID의 JSON 객체여야 합니다.")
    MACHINE_ID_TO_ASSET_ID = {int(machine): asset for machine, asset in configured.items()}


def _decode_result(result, tool: str) -> dict[str, Any]:
    if result.isError:
        raise RuntimeError(f"CMMS {tool} 실패: {result.content}")
    for item in result.content:
        if getattr(item, "type", None) == "text":
            value = json.loads(item.text)
            if isinstance(value, dict):
                return value
    raise RuntimeError(f"CMMS {tool}: JSON 결과 없음")


async def _call_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    assert CMMS_MCP_URL is not None
    headers = {"Authorization": f"Bearer {CMMS_MCP_TOKEN}"}
    async with streamablehttp_client(CMMS_MCP_URL, headers=headers, timeout=10, sse_read_timeout=30) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            return _decode_result(await session.call_tool(tool, arguments), tool)


async def _create_work_order(title: str, description: str, asset_id: int | None) -> dict[str, Any]:
    # 호출부(push_work_order)가 CMMS_MCP_URL/TOKEN 둘 다 설정돼 있을 때만 이 함수를 부른다 -
    # 그 전제를 mypy에도 알려준다(2026-09-22 타입체커 도입 중 발견: 예전엔 이 전제가
    # 코드로 보장 안 되고 눈으로만 확인해야 했다).
    arguments: dict[str, Any] = {"title": title, "description": description, "priority": "HIGH"}
    if asset_id is not None:
        arguments["assetId"] = asset_id
    created = await _call_tool("create-work-order", arguments)
    if type(created.get("workOrderId")) is not int or created["workOrderId"] <= 0:
        raise RuntimeError("CMMS create-work-order: 유효한 workOrderId 없음")
    return created


def _format_for_cmms(text: str) -> str:
    """Atlas CMMS의 작업지시서 상세 화면은 description을 일반 텍스트 컴포넌트로
    렌더링해서 개행이 살아남지 않고 한 줄로 뭉개진다 - 그 상태에서도 항목이 구분되게,
    부품 블록 사이는 굵은 구분자, 같은 블록 안 필드는 가운뎃점, 번호 매김
    조치사항(`1. ... 2. ...`)은 화살표 불릿으로 바꾼다."""
    text = re.sub(r"\n\n+", "  ▌  ", text)
    text = re.sub(r"(?<=\s)(\d+)\.\s", r" ▸ ", text)
    return text.replace("\n", "  ·  ")


def _is_loopback_url(url: str) -> bool:
    # 2026-09-29 CP-M2 교차 검토 지적: 이 목록이 offline_guard.OFFLINE_ALLOWED_HOSTS와
    # 별개로 하드코딩돼 있었다 - 이제 그 상수를 그대로 재사용해서 "유일한 정의처"를
    # 실제로 지킨다.
    # 2026-09-29 security-reviewer 인계: urllib로 호스트만 뽑아 비교하면
    # `http://evil\@localhost/` 같은 URL을 localhost로 오판한다(실제 접속은 다른
    # 파서가 함) - 모호한 URL을 먼저 거르는 offline_guard.is_allowed_url()로 통일.
    return offline_guard.is_allowed_url(url)


def delivery_record(machine_id: int, work_order_text: str, delivery_key: str | None = None) -> dict:
    return cmms_delivery.get(_delivery_key(machine_id, work_order_text, delivery_key))


def _delivery_key(machine_id: int, text: str, key: str | None) -> str:
    # Scope persisted IDs to the configured CMMS and the approved content.
    return hashlib.sha256(json.dumps([CMMS_MCP_URL, machine_id, key, text], ensure_ascii=False).encode()).hexdigest()


def push_work_order(machine_id: int, work_order_text: str, *,
                    payload: dict | None = None, delivery_key: str | None = None,
                    refresh_tasks: bool = False) -> str:
    """항상 긴급+승인된 work_order에서만 호출된다(finalize_node 참고) - priority가
    HIGH로 고정인 이유.

    2026-09-29 CP-M2 교차 검토 지적 반영: 예전엔 스킵/차단/전송을 구분 없이 전부
    None을 반환했다 - 호출부(finalize_node)가 "예외 없이 반환 = 성공"으로 감사
    로그에 기록해서, CMMS가 애초에 설정 안 된 상태에서도 "성공"이 찍히는 거짓
    기록을 냈다. 이제 상태 문자열을 반환해서 호출부가 실제로 무슨 일이 있었는지
    정확히 기록할 수 있게 한다. 반환값: "sent"(실제 전송 시도, 예외 없으면 성공) |
    "skipped_unconfigured"(URL/TOKEN 미설정) | "blocked_insecure_url"(평문 HTTP
    비루프백) | "blocked_offline"(OFFLINE=1인데 허용 목록 밖 호스트)."""
    if not CMMS_MCP_URL or not CMMS_MCP_TOKEN:
        return "skipped_unconfigured"
    # CMMS_MCP_TOKEN이 평문 HTTP로 그대로 나간다 - 루프백이 아닌 주소에 http://를 쓰면
    # 실수로 토큰을 네트워크에 노출시키게 되므로 아예 막는다 (security-reviewer 지적,
    # 2026-09-18). 지금(Atlas-MCP를 같은 호스트에 두는 구성)은 http://localhost가 정상이다.
    # 2026-09-29 security-reviewer 인계: startswith("http://")는 대소문자를 구분해서
    # `HTTP://evil.example/`이 평문 차단을 그대로 통과했다(토큰 평문 노출). 스킴은
    # 대소문자 무관이므로 파싱한 뒤 소문자로 비교하고, 모호한 URL은 여기서도 막는다.
    if offline_guard.is_ambiguous_url(CMMS_MCP_URL):
        logger.warning("[CMMS push 차단] CMMS_MCP_URL이 모호한 형식(userinfo/백슬래시/공백/비HTTP 스킴) - 요청 차단")
        return "blocked_insecure_url"
    if urlparse(CMMS_MCP_URL).scheme.lower() == "http" and not _is_loopback_url(CMMS_MCP_URL):
        logger.warning("[CMMS push 실패] CMMS_MCP_URL이 루프백이 아닌데 http://를 사용 - 토큰 평문 노출 위험, 요청 차단")
        return "blocked_insecure_url"
    # 2026-09-29 CP-M2 교차 검토 지적: 허용 목록은 지금까지 테스트(pytest-socket)에서만
    # 검사됐고 실제 기동 경로에는 전혀 적용되지 않았다 - CMMS_MCP_URL을 외부 주소로
    # 잘못 설정해도 OFFLINE=1이면 그대로 나갔다. 여기서 직접 막는다.
    if offline_guard.is_offline() and not _is_loopback_url(CMMS_MCP_URL):
        logger.warning(f"[CMMS push 차단] OFFLINE=1인데 CMMS_MCP_URL이 허용 목록 밖: {CMMS_MCP_URL}")
        return "blocked_offline"
    if not payload or not payload.get("tasks"):
        return "blocked_missing_tasks"
    key = _delivery_key(machine_id, work_order_text, delivery_key)
    saved = cmms_delivery.claim(key, json.dumps({**payload, "original": work_order_text}, ensure_ascii=False))
    if saved["status"] == "sent" and not refresh_tasks:
        return "sent"
    if saved["status"] in ("creating", "creation_unknown"):
        # A lost creation response may still have created an order; do not blindly recreate.
        return "creation_unknown"
    if saved["status"] == "new":
        try:
            created = asyncio.run(_create_work_order(payload["title"], payload["description"],
                                                     MACHINE_ID_TO_ASSET_ID.get(machine_id)))
            cmms_delivery.record(key, "created", created["workOrderId"], created.get("id"))
            saved = cmms_delivery.get(key)
        except Exception:
            cmms_delivery.record(key, "creation_unknown", error="생성 응답 미확인; Atlas 확인 후 복구 필요")
            raise
    # Keep the actual retry payload, including upgraded details, for later recovery.
    cmms_delivery.record(key, "created", payload_json=json.dumps(
        {**payload, "original": work_order_text}, ensure_ascii=False))
    try:
        registered = asyncio.run(_call_tool("add-work-order-tasks", {
            "workOrderId": saved["work_order_id"], "tasks": payload["tasks"]}))
        if registered.get("verified") is not True:
            raise RuntimeError("CMMS Tasks 저장 검증 실패")
    except Exception:
        cmms_delivery.record(key, "partial_tasks", error="작업지시서 생성됨; Tasks 등록 재시도 필요")
        logger.exception("[CMMS] 작업지시서 %s의 Tasks 등록 실패", saved["display_id"])
        return "partial_tasks"
    cmms_delivery.record(key, "sent")
    return "sent"
