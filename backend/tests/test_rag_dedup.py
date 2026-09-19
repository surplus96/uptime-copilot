"""회귀 테스트(무거움 - 실제 임베딩 모델 필요, 3개 가벼운 테스트와 분리):
2026-09-18 세션에서 pipeline-optimizer가 찾아낸 RAG 중복 적재 버그.

Chroma.from_documents()는 호출할 때마다 새 UUID로 추가만 하고 기존 내용을 지우지
않는다 - 서버를 재시작할 때마다(uvicorn --reload 포함) 같은 청크가 계속 쌓여서,
실측 원본 4개 청크가 168개로 누적됐었다(42배). 이 테스트는 initialize_rag()를
격리된 임시 Chroma 디렉토리에 대해 두 번 호출해서(=재시작 시뮬레이션), 두 번째
호출이 컬렉션을 중복시키지 않는지 확인한다.
"""
import chromadb
import pytest

from rag import rag_service


@pytest.fixture
def isolated_persist_dir(tmp_path, monkeypatch):
    persist_dir = str(tmp_path / "chroma_db")
    monkeypatch.setattr(rag_service, "PERSIST_DIR", persist_dir)
    return persist_dir


def test_initialize_rag_does_not_duplicate_on_second_call(isolated_persist_dir):
    # ChatOpenAI 클라이언트 생성 자체는 네트워크 호출이 없다(실제 API 호출을 안 하므로
    # 더미 키로 충분) - 이 테스트가 확인하려는 건 Chroma 적재 로직이지 LLM 응답이 아니다.
    rag_service.initialize_rag("sk-dummy-key-for-client-construction-only", "gpt-5.6-luna")

    client = chromadb.PersistentClient(path=isolated_persist_dir)
    first_count = client.get_collection("langchain").count()
    assert first_count > 0, "최초 적재는 실제로 청크가 들어가야 한다"

    # 재시작 시뮬레이션 - 같은 PERSIST_DIR로 두 번째 초기화
    rag_service.initialize_rag("sk-dummy-key-for-client-construction-only", "gpt-5.6-luna")

    second_count = client.get_collection("langchain").count()
    assert second_count == first_count, (
        f"재시작 시 청크가 중복 적재되면 안 된다 (1차: {first_count}, 2차: {second_count})"
    )


def test_initialize_rag_reingest_uses_correct_chunk_count(isolated_persist_dir):
    """최초 적재된 개수가 실제 docs/ 폴더를 청킹한 결과와 정확히 일치하는지 -
    '뭔가 들어가긴 했다' 수준이 아니라 정확한 개수 검증."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    raw_documents = rag_service._load_documents()
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    expected_chunks = len(splitter.split_documents(raw_documents))

    rag_service.initialize_rag("sk-dummy-key-for-client-construction-only", "gpt-5.6-luna")

    client = chromadb.PersistentClient(path=isolated_persist_dir)
    assert client.get_collection("langchain").count() == expected_chunks
