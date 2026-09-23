"""회귀 테스트: 2026-09-18 세션에서 code-quality-reviewer + pipeline-optimizer가
독립적으로 찾아낸 버그 - MCP는 도구 실행 실패(Atlas 쪽 거부 등)를 예외가 아니라
성공 응답 안의 isError 필드로 보고하는데, _create_work_order()가 call_tool()의
반환값을 아예 버려서 CMMS가 거부해도 성공으로 착각했다. 실제로 존재하지 않는
assetId로 Atlas-MCP를 호출해 isError: true를 재현한 뒤 고쳤음(docs/design/PHASE_7_PLAN.md 참고) -
여기서는 그 실제 재현을 네트워크 없이 mock으로 반복 가능하게 만든다.
"""
import pytest

import cmms_client


class _FakeCallToolResult:
    def __init__(self, is_error: bool, content="상세 내용"):
        self.isError = is_error
        self.content = content


class _FakeSession:
    def __init__(self, result):
        self._result = result

    async def initialize(self):
        pass

    async def call_tool(self, name, arguments):
        return self._result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeStreams:
    async def __aenter__(self):
        return ("read", "write", None)

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_create_work_order_raises_on_isError(monkeypatch):
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", "http://localhost:3100/mcp")
    fake_result = _FakeCallToolResult(is_error=True, content="Atlas API request failed: POST /work-orders (500)")
    monkeypatch.setattr(cmms_client, "streamablehttp_client", lambda *a, **kw: _FakeStreams())
    monkeypatch.setattr(cmms_client, "ClientSession", lambda *a, **kw: _FakeSession(fake_result))

    with pytest.raises(RuntimeError, match="CMMS create-work-order 실패"):
        await cmms_client._create_work_order("제목", "설명", asset_id=1)


@pytest.mark.asyncio
async def test_create_work_order_succeeds_when_not_isError(monkeypatch):
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", "http://localhost:3100/mcp")
    fake_result = _FakeCallToolResult(is_error=False)
    monkeypatch.setattr(cmms_client, "streamablehttp_client", lambda *a, **kw: _FakeStreams())
    monkeypatch.setattr(cmms_client, "ClientSession", lambda *a, **kw: _FakeSession(fake_result))

    await cmms_client._create_work_order("제목", "설명", asset_id=1)  # 예외 없이 반환돼야 함


def test_format_for_cmms_survives_whitespace_collapse():
    """가독성 수정(2026-09-18): Atlas CMMS 상세 화면은 description을 일반 텍스트로
    렌더링해서 개행이 공백 하나로 뭉개진다 - 그래도 항목이 구분되게 블록/필드/조치
    각각을 살아남는 구분자(▌ · ▸)로 바꿔야 한다."""
    import cmms_client

    raw = "설비 #12\n\n[부품] 베어링\n[조치사항] 1. 육안 검사 2. 교체\n[긴급도] 긴급"
    formatted = cmms_client._format_for_cmms(raw)

    assert "\n" not in formatted
    assert "▌" in formatted
    assert "▸ 육안 검사" in formatted
    assert "▸ 교체" in formatted


def test_push_work_order_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", None)
    monkeypatch.setattr(cmms_client, "CMMS_MCP_TOKEN", None)
    cmms_client.push_work_order(84, "작업지시서 텍스트")  # 예외 없이 조용히 반환


def test_push_work_order_blocks_non_loopback_http(monkeypatch, caplog):
    """회귀 대상(security-reviewer): 루프백이 아닌 주소에 http://를 쓰면 토큰이
    평문으로 나가므로 아예 차단해야 한다."""
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", "http://example.com/mcp")
    monkeypatch.setattr(cmms_client, "CMMS_MCP_TOKEN", "sometoken")

    cmms_client.push_work_order(84, "작업지시서 텍스트")

    assert "차단" in caplog.text



def test_push_work_order_allows_loopback_http(monkeypatch):
    """루프백 주소는 http://라도 차단되면 안 된다 - 지금 실제 배포 구성이 이거다."""
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", "http://localhost:3100/mcp")
    monkeypatch.setattr(cmms_client, "CMMS_MCP_TOKEN", "sometoken")
    assert cmms_client._is_loopback_url(cmms_client.CMMS_MCP_URL) is True
