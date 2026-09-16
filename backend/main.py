"""
FastAPI 백엔드

실행 방법:
    uvicorn main:app --reload --port 8000
"""

import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from openai import OpenAI
from pydantic import BaseModel, field_validator
import logging

from langsmith import Client, traceable
from langsmith.anonymizer import create_anonymizer
from langsmith.wrappers import wrap_openai


from core.prompts import SYSTEM_PROMPT
from core.harness import (
    validate_input,
    check_output_forbidden_words,
    judge_response_quality,
    judge_faithfulness,
    should_retrieve,
    HarnessRejectedError,
    PII_PATTERNS,
)
from rag import rag_service
from agent import agent_service


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")

# LangSmith로 나가는 트레이스에서 PII를 마스킹 (harness의 PII_PATTERNS 재사용 - 같은 기준으로 응답/트레이스 양쪽 방어)
anonymizer = create_anonymizer([
    {"pattern": pattern.pattern, "replace": f"<{label}>"}
    for label, pattern in PII_PATTERNS.items()
])
langsmith_client = Client(anonymizer=anonymizer)

# 서버 전역에서 재사용할 OpenAI 클라이언트 - wrap_openai로 감싸서 judge 호출까지 자동 추적 + 익명화
client = wrap_openai(
    OpenAI(api_key=OPENAI_API_KEY),
    tracing_extra={"client": langsmith_client},
)


# 세션별 대화 상태를 메모리에 보관 (재시작하면 초기화됨 -> 추후 Redis/DB 등으로 교체 가능)
# OpenAI는 무상태 API라, {"model": ..., "messages": [...]} 형태로 히스토리를 직접 관리한다.
chat_sessions: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    rag_service.initialize_rag(OPENAI_API_KEY, DEFAULT_MODEL, langsmith_client)
    agent_service.initialize_agent(langsmith_client)  # <- 추가
    yield
    chat_sessions.clear()



app = FastAPI(title="AI Agent RAG Backend (OpenAI)", lifespan=lifespan)

# ---------- 커스텀 예외 ---------------


class LLMAPIError(Exception):
    """LLM API 호출 자체가 실패했을 때 발생시키는 도메인 예외."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class SessionNotFoundError(Exception):
    """존재하지 않는 session_id를 조회/삭제하려 할 때 발생시키는 도메인 예외."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"session_id={session_id} not found")


# Streamlit(로컬 개발 시 기본 8501 포트)에서의 요청을 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(LLMAPIError)
async def llm_api_error_handler(request: Request, exc: LLMAPIError):
    logger.error(f"LLM API 호출 실패: {exc.detail}")
    return JSONResponse(
        status_code=502,
        content={"error": "llm_api_error", "detail": exc.detail},
    )


@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"error": "session_not_found", "session_id": exc.session_id},
    )


@app.exception_handler(HarnessRejectedError)
async def harness_rejected_handler(request: Request, exc: HarnessRejectedError):
    logger.warning(f"하네스 검증 실패: {exc.reason}")
    return JSONResponse(
        status_code=400,
        content={"error": "harness_rejected", "reason": exc.reason},
    )


# ---------- 요청/응답 스키마 ----------

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # 없으면 새 세션 생성
    model: str | None = None

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message는 빈 문자열이거나 공백만으로 구성될 수 없습니다.")
        return v


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class NewSessionResponse(BaseModel):
    session_id: str


class RAGRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question은 빈 문자열이거나 공백만으로 구성될 수 없습니다.")
        return v

class RAGResponse(BaseModel):
    question: str
    answer: str
    context: str | None = None


class AgentQueryRequest(BaseModel):
    message: str
    thread_id: str

class AgentResumeRequest(BaseModel):
    thread_id: str
    approved: bool

class AgentQueryRequest(BaseModel):
    message: str
    thread_id: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message는 빈 문자열이거나 공백만으로 구성될 수 없습니다.")
        return v


# ---------- 유틸 ----------

def get_or_create_session(session_id: str | None, model: str | None) -> tuple[str, dict]:
    """session_id가 없거나 존재하지 않으면 새 세션(모델 + 메시지 히스토리)을 만들어 반환."""
    if session_id and session_id in chat_sessions:
        return session_id, chat_sessions[session_id]

    new_id = session_id or str(uuid.uuid4())
    chat_sessions[new_id] = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
    }
    logger.info(f"새 세션 생성: {new_id} (model={model or DEFAULT_MODEL})")
    return new_id, chat_sessions[new_id]


# ---------- 라우트 ----------

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/session", response_model=NewSessionResponse)
def create_session(model: str | None = None):
    """새 채팅 세션을 생성하고 session_id를 반환."""
    session_id, _ = get_or_create_session(None, model)
    return NewSessionResponse(session_id=session_id)


@app.post("/chat", response_model=ChatResponse)
@traceable(name="chat", client=langsmith_client)
def chat(req: ChatRequest):
    """세션에 메시지를 보내고 전체 응답을 한 번에 받는다 (스트리밍 아님)."""
    session_id, session = get_or_create_session(req.session_id, req.model)

    # 1단계: 검증 (하네스) - 모델을 부르기 전에 입력을 먼저 검사
    validate_input(req.message)

    # 대화 히스토리에 사용자 메시지 추가 (OpenAI는 무상태이므로 매번 전체 히스토리를 보낸다)
    session["messages"].append({"role": "user", "content": req.message})

    # 2단계: 제안 (모델) - 모델이 응답을 "제안"한다
    try:
        completion = client.chat.completions.create(
            model=session["model"],
            messages=session["messages"],
        )
    except Exception as e:
        session["messages"].pop()  # 실패한 사용자 턴은 히스토리에서 롤백
        raise LLMAPIError(str(e))

    reply = completion.choices[0].message.content
    session["messages"].append({"role": "assistant", "content": reply})

    # 3단계: 검증 (하네스) - 모델이 제안한 응답을 실행(반환)하기 전에 검사
    check_output_forbidden_words(reply)

    # 3.5단계: 검증 (하네스) - inferential (LLM-as-judge)
    judge_result = judge_response_quality(client, req.message, reply)
    if not judge_result.get("pass", True):
        raise HarnessRejectedError(f"LLM-as-judge 품질 기준 미달: {judge_result.get('reason')}")

    # 4단계: 실행 - 검증을 통과한 응답만 클라이언트에 반환
    return ChatResponse(session_id=session_id, reply=reply)


@app.post("/chat/stream")
@traceable(name="chat_stream", client=langsmith_client)
def chat_stream(req: ChatRequest):
    """세션에 메시지를 보내고 스트리밍으로 응답을 받는다 (Streamlit st.write_stream과 호환)."""
    session_id, session = get_or_create_session(req.session_id, req.model)

    validate_input(req.message)

    session["messages"].append({"role": "user", "content": req.message})

    def event_generator():
        full_reply_parts: list[str] = []
        try:
            stream = client.chat.completions.create(
                model=session["model"],
                messages=session["messages"],
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_reply_parts.append(delta)
                    yield delta
        except Exception as e:
            logger.error(f"스트리밍 중 OpenAI API 호출 실패: {e}")
            session["messages"].pop()  # 실패한 사용자 턴은 히스토리에서 롤백
            yield f"\n[오류] OpenAI API 호출 실패: {e}"
            return

        # 스트림이 끝까지 정상적으로 소비된 뒤에만 히스토리에 어시스턴트 응답을 기록한다
        session["messages"].append({"role": "assistant", "content": "".join(full_reply_parts)})

    return StreamingResponse(event_generator(), media_type="text/plain")


@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    if session_id not in chat_sessions:
        raise SessionNotFoundError(session_id)  # HTTPException 대신 도메인 예외
    del chat_sessions[session_id]
    logger.info(f"세션 삭제됨: {session_id}")
    return {"status": "deleted", "session_id": session_id}


@app.post("/rag/query", response_model=RAGResponse)
@traceable(name="rag_query", client=langsmith_client)
def rag_query(req: RAGRequest):
    """docs/ 폴더에 적재된 문서를 근거로 질문에 답한다 (RAG)."""
    validate_input(req.question)

    if not should_retrieve(client, req.question):
        completion = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": req.question},
            ]
        )
        answer = completion.choices[0].message.content
        return RAGResponse(question=req.question, answer=answer, context=None)

    try:
        context, answer = rag_service.answer_with_context(req.question)
    except Exception as e:
        raise LLMAPIError(str(e))

    check_output_forbidden_words(answer)

    faithfulness_result = judge_faithfulness(client, context, answer)
    if not faithfulness_result.get("pass", True):
        raise HarnessRejectedError(
            f"RAG 답변이 검색 문맥에 근거하지 않음(hallucination 의심): {faithfulness_result.get('reason')}"
        )

    return RAGResponse(question=req.question, answer=answer, context=context)



@app.post("/agent/query")
def agent_query(req: AgentQueryRequest):
    """멀티에이전트 그래프에 질문을 보낸다. 긴급 사안이면 승인 대기 상태로 응답할 수 있다."""
    validate_input(req.message)
    return agent_service.start_agent(req.message, req.thread_id)


@app.post("/agent/resume")
def agent_resume(req: AgentResumeRequest):
    """승인 대기 중인 요청에 사람의 결정을 전달해서 재개한다."""
    return agent_service.resume_agent(req.thread_id, req.approved)