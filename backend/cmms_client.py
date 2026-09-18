"""
Atlas CMMS 작업지시서 push - 결정론적 트리거(긴급 승인)에서만 호출된다.
Stage 1(notify.py)과 같은 원칙: 이미 승인이 끝난 뒤의 고정된 행동이라 에이전트가 도구를
고를 필요가 없다. 그래서 langchain-mcp-adapters(LLM에게 도구 선택을 맡기는 프레임워크) 대신
mcp SDK로 정해진 도구 하나를 직접, 결정론적으로 호출한다 (PHASE_7_PLAN.md Stage 2 참고).
CMMS 장애가 승인 흐름을 막으면 안 되므로 예외는 호출부(finalize_node)에서 삼킨다.
"""

import asyncio
import os

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


load_dotenv()
CMMS_MCP_URL = os.getenv("CMMS_MCP_URL")
CMMS_MCP_TOKEN = os.getenv("CMMS_MCP_TOKEN")


# 프로토타입 단계 - 설비 번호 -> Atlas assetId 매핑이 지금은 테스트 자산 하나뿐이다.
# 실제 벤더 연동 시 Atlas의 자산 규칙에 맞춰 조회하거나 매핑 테이블로 교체해야 한다.
MACHINE_ID_TO_ASSET_ID = {84: 1}


async def _create_work_order(title: str, description: str, asset_id: int | None) -> None:
    headers = {"Authorization": f"Bearer {CMMS_MCP_TOKEN}"}
    async with streamablehttp_client(CMMS_MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            arguments = {"title": title, "description": description, "priority": "HIGH"}
            if asset_id is not None:
                arguments["assetId"] = asset_id
            result = await session.call_tool("create-work-order", arguments)
            # MCP는 도구 실행 실패(Atlas 쪽 거부 등)를 예외가 아니라 성공 응답 안의
            # isError 필드로 보고한다 - 이걸 확인 안 하면 CMMS가 거부해도 우리 쪽은
            # 성공으로 착각한다 (code-quality-reviewer + pipeline-optimizer 공통 지적,
            # 2026-09-18, atlas-mcp 실제 소스로도 재확인됨).
            if result.isError:
                raise RuntimeError(f"CMMS create-work-order 실패: {result.content}")


def _is_loopback_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    return host in ("localhost", "127.0.0.1")


def push_work_order(machine_id: int, work_order_text: str) -> None:
    """항상 긴급+승인된 work_order에서만 호출된다(finalize_node 참고) - priority가
    HIGH로 고정인 이유."""
    if not CMMS_MCP_URL or not CMMS_MCP_TOKEN:
        return
    # CMMS_MCP_TOKEN이 평문 HTTP로 그대로 나간다 - 루프백이 아닌 주소에 http://를 쓰면
    # 실수로 토큰을 네트워크에 노출시키게 되므로 아예 막는다 (security-reviewer 지적,
    # 2026-09-18). 지금(Atlas-MCP를 같은 호스트에 두는 구성)은 http://localhost가 정상이다.
    if CMMS_MCP_URL.startswith("http://") and not _is_loopback_url(CMMS_MCP_URL):
        print(f"[CMMS push 실패] CMMS_MCP_URL이 루프백이 아닌데 http://를 사용 - 토큰 평문 노출 위험, 요청 차단")
        return
    title = f"설비 #{machine_id} 긴급 정비"
    asset_id = MACHINE_ID_TO_ASSET_ID.get(machine_id)
    asyncio.run(_create_work_order(title, work_order_text, asset_id))