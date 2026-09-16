"""
RAG 서비스 모듈: docs/ 폴더의 문서를 로드 -> 청킹 -> 임베딩 -> Chroma(영속) 적재
-> LCEL RAG 체인 구성. main.py의 lifespan에서 서버 시작 시 1회 초기화되고,
/rag/query 라우트에서 재사용된다.

[OpenAI 호환 버전] 생성 모델만 OpenAI(langchain_openai.ChatOpenAI)로 교체했다.
임베딩(HuggingFaceEmbeddings, multilingual-e5-small)은 로컬 모델이라 특정 LLM
제공사와 무관하므로 그대로 재사용한다 - "임베딩은 로컬/오픈소스, 생성은 클라우드
LLM"이라는 조합도 실무에서 흔한 선택이다.
"""

import glob
import logging
import os
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_classic.retrievers import MultiQueryRetriever
from langchain_core.tracers import LangChainTracer
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever




logger = logging.getLogger(__name__)

# CWD(실행 위치)가 아니라 이 파일 자신의 위치를 기준으로 경로를 잡는다 -
# main.py를 어디서 실행하든, rag_service.py가 어느 폴더로 옮겨지든 항상 정확한 경로를 가리킨다.
_MODULE_DIR = Path(__file__).parent
DOCS_DIR = str(_MODULE_DIR / "docs")
PERSIST_DIR = str(_MODULE_DIR / "chroma_db")

_rag_chain = None  # 서버 시작 시 1회만 구축해서 재사용 (요청마다 재구축 방지)
_retriever = None
_tracer = None # LangSmith 익명화 트레이서 (initialize_rag에서 설정)


def _load_documents() -> list[Document]:
    """docs/ 폴더의 모든 .txt 파일을 읽어 Document 리스트로 반환."""
    documents = []
    for path in glob.glob(os.path.join(DOCS_DIR, "*.txt")):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        documents.append(Document(page_content=text, metadata={"source": path}))

    return documents


def initialize_rag(openai_api_key: str, model_name: str, langsmith_client=None) -> None:
    """서버 시작 시 1회 호출: 문서 로드 -> 청킹 -> 임베딩 -> 벡터DB -> RAG 체인 구성."""
    global _rag_chain, _retriever, _tracer

    if langsmith_client is not None:
        _tracer = LangChainTracer(client=langsmith_client)

    raw_documents = _load_documents()
    logger.info(f"RAG용 원본 문서 {len(raw_documents)}개 로드")

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = splitter.split_documents(raw_documents)
    logger.info(f"청킹 결과: {len(chunks)}개 청크")

    embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
    vectorstore = Chroma.from_documents(chunks, embeddings, persist_directory=PERSIST_DIR)
    dense_retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

    # Sparse: BM25 키워드 검색 - 모델명/오류코드처럼 정확한 문자열 매칭이 중요한 질의에 강함
    bm25_retriever = BM25Retriever.from_documents(chunks)
    bm25_retriever.k = 3

    # 하이브리드: Dense(의미)와 Sparse(키워드) 결과를 가중치 0.5:0.5로 합친다
    hybrid_retriever = EnsembleRetriever(
        retrievers=[dense_retriever, bm25_retriever],
        weights=[0.5, 0.5],
    )

    model = ChatOpenAI(model=model_name, api_key=openai_api_key)

    # Multi-Query가 하이브리드 검색기를 감싼다: 재작성된 질문마다 Dense+Sparse를 함께 수행
    retriever = MultiQueryRetriever.from_llm(retriever=hybrid_retriever, llm=model)
    _retriever = retriever  # get_context()에서도 재작성된 검색 결과를 그대로 재사용
    
    rag_prompt = ChatPromptTemplate([
        ("system", "당신은 주어진 [문맥]만 근거로 답하는 어시스턴트입니다. "
                   "문맥에 없는 내용은 모른다고 답하세요.\n\n[문맥]\n{context}"),
        ("user", "{question}"),
    ])

    # 검색(retriever)은 체인에서 분리한다 - 검색은 answer_with_context()에서 딱 1번만 수행하고,
    # 그 결과를 이 체인에 {"context": ..., "question": ...}로 직접 넘긴다.
    _rag_chain = rag_prompt | model | StrOutputParser()
    logger.info("RAG 파이프라인 초기화 완료 (Multi-Query 적용, OpenAI)")


def answer_with_context(question: str) -> tuple[str, str]:
    """검색을 1회만 수행하고, 그 문맥으로 답변을 생성한다."""
    if _retriever is None or _rag_chain is None:
        raise RuntimeError("RAG 파이프라인이 초기화되지 않았습니다.")

    config = {"callbacks": [_tracer]} if _tracer else {}

    docs = _retriever.invoke(question, config=config)  # 검색 -> 딱 1번
    context = "\n\n".join(d.page_content for d in docs)

    answer = _rag_chain.invoke({"context": context, "question": question}, config=config)
    return context, answer
