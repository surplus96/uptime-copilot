"""
Atlas CMMS 작업지시서 push - 결정론적 트리거(긴급 승인)에서만 호출된다.
Stage 1(notify.py)과 같은 원칙: 이미 승인이 끝난 뒤의 고정된 행동이라 에이전트가 도구를
고를 필요가 없다. 그래서 langchain-mcp-adapters(LLM에게 도구 선택을 맡기는 프레임워크) 대신
mcp SDK로 정해진 도구 하나를 직접, 결정론적으로 호출한다 (docs/design/PHASE_7_PLAN.md Stage 2 참고).
CMMS 장애가 승인 흐름을 막으면 안 되므로 예외는 호출부(finalize_node)에서 삼킨다.
"""

import asyncio
import logging
import os
import re
from datetime import timedelta

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()
logger = logging.getLogger(__name__)
CMMS_MCP_URL = os.getenv("CMMS_MCP_URL")
CMMS_MCP_TOKEN = os.getenv("CMMS_MCP_TOKEN")


# 프로토타입 단계 - 설비 번호 -> Atlas assetId 매핑이 지금은 테스트 자산 하나뿐이다.
# 실제 벤더 연동 시 Atlas의 자산 규칙에 맞춰 조회하거나 매핑 테이블로 교체해야 한다.
MACHINE_ID_TO_ASSET_ID = {84: 1}


async def _create_work_order(title: str, description: str, asset_id: int | None) -> None:
    # 호출부(push_work_order)가 CMMS_MCP_URL/TOKEN 둘 다 설정돼 있을 때만 이 함수를 부른다 -
    # 그 전제를 mypy에도 알려준다(2026-09-22 타입체커 도입 중 발견: 예전엔 이 전제가
    # 코드로 보장 안 되고 눈으로만 확인해야 했다).
    assert CMMS_MCP_URL is not None
    headers = {"Authorization": f"Bearer {CMMS_MCP_TOKEN}"}
    # 명시적 타임아웃 없으면 SSE 기본값(300초)까지 걸릴 수 있음 - 프론트 /agent/resume
    # 타임아웃(60초)보다 훨씬 짧게 잡아서, CMMS가 응답만 느려도 전체 승인 흐름이
    # 오래 안 걸리게 한다 (pipeline-optimizer 지적, 2026-09-18).
    async with streamablehttp_client(CMMS_MCP_URL, headers=headers, timeout=10, sse_read_timeout=15) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
            await session.initialize()
            arguments: dict[str, str | int] = {"title": title, "description": description, "priority": "HIGH"}
            if asset_id is not None:
                arguments["assetId"] = asset_id
            result = await session.call_tool("create-work-order", arguments)
            if result.isError:
                raise RuntimeError(f"CMMS create-work-order 실패: {result.content}")


def _format_for_cmms(text: str) -> str:
    """Atlas CMMS의 작업지시서 상세 화면은 description을 일반 텍스트 컴포넌트로
    렌더링해서 개행이 살아남지 않고 한 줄로 뭉개진다 - 그 상태에서도 항목이 구분되게,
    부품 블록 사이는 굵은 구분자, 같은 블록 안 필드는 가운뎃점, 번호 매김
    조치사항(`1. ... 2. ...`)은 화살표 불릿으로 바꾼다."""
    text = re.sub(r"\n\n+", "  ▌  ", text)
    text = re.sub(r"(?<=\s)(\d+)\.\s", r" ▸ ", text)
    return text.replace("\n", "  ·  ")


def _is_loopback_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    # host.docker.internal은 Docker Desktop이 컨테이너 -> 호스트 방향으로만 열어주는
    # 별칭이라 실제 네트워크로 나가지 않는다 - localhost와 같은 신뢰 경계로 취급한다.
    return host in ("localhost", "127.0.0.1", "host.docker.internal")


def push_work_order(machine_id: int, work_order_text: str) -> None:
    """항상 긴급+승인된 work_order에서만 호출된다(finalize_node 참고) - priority가
    HIGH로 고정인 이유."""
    if not CMMS_MCP_URL or not CMMS_MCP_TOKEN:
        return
    # CMMS_MCP_TOKEN이 평문 HTTP로 그대로 나간다 - 루프백이 아닌 주소에 http://를 쓰면
    # 실수로 토큰을 네트워크에 노출시키게 되므로 아예 막는다 (security-reviewer 지적,
    # 2026-09-18). 지금(Atlas-MCP를 같은 호스트에 두는 구성)은 http://localhost가 정상이다.
    if CMMS_MCP_URL.startswith("http://") and not _is_loopback_url(CMMS_MCP_URL):
        logger.warning("[CMMS push 실패] CMMS_MCP_URL이 루프백이 아닌데 http://를 사용 - 토큰 평문 노출 위험, 요청 차단")
        return
    title = f"설비 #{machine_id} 긴급 정비"
    asset_id = MACHINE_ID_TO_ASSET_ID.get(machine_id)
    asyncio.run(_create_work_order(title, _format_for_cmms(work_order_text), asset_id))